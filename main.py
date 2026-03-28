import os
import json
import time
import uuid
import secrets
import subprocess
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Request, File, UploadFile, Form
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(redirect_slashes=False)

FILES_DIR = "files"
os.makedirs(FILES_DIR, exist_ok=True)
app.mount("/files", StaticFiles(directory=FILES_DIR), name="files")

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL")
TIKTOK_CLIENT_KEY = os.environ.get("TIKTOK_CLIENT_KEY")
TIKTOK_CLIENT_SECRET = os.environ.get("TIKTOK_CLIENT_SECRET")
TIKTOK_REDIRECT_URI = os.environ.get("TIKTOK_REDIRECT_URI")
DEFAULT_SCOPE = "user.info.basic,video.upload,video.publish"
TOKENS_PATH = os.environ.get("TOKENS_PATH", "tokens.json")
TOKEN_SKEW_SECONDS = 120

USED_CODES = {}
USED_CODES_TTL_SECONDS = 10 * 60


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def require_env(value, name):
    if not value:
        return None, JSONResponse({"ok": False, "error": f"Missing env: {name}"}, status_code=500)
    return value, None


def load_tokens():
    if not os.path.exists(TOKENS_PATH):
        return None
    with open(TOKENS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_tokens(tokens):
    with open(TOKENS_PATH, "w", encoding="utf-8") as f:
        json.dump(tokens, f, ensure_ascii=False, indent=2)


def cleanup_used_codes():
    now = time.time()
    for k in [k for k, ts in USED_CODES.items() if (now - ts) > USED_CODES_TTL_SECONDS]:
        USED_CODES.pop(k, None)


def token_expired(tokens):
    return time.time() >= float(tokens.get("expires_at", 0)) - TOKEN_SKEW_SECONDS


def first_present(*values, default=None):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip() == "":
                continue
            return value
        return value
    return default


def to_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return bool(value)


def normalize_privacy_level(value, default="SELFONLY"):
    if value is None:
        return default

    v = str(value).strip()
    if not v:
        return default

    mapping = {
        "SELF_ONLY": "SELFONLY",
        "SELFONLY": "SELFONLY",
        "PUBLIC_TO_EVERYONE": "PUBLICTOEVERYONE",
        "PUBLICTOEVERYONE": "PUBLICTOEVERYONE",
        "MUTUAL_FOLLOW_FRIENDS": "MUTUALFOLLOWFRIENDS",
        "MUTUALFOLLOWFRIENDS": "MUTUALFOLLOWFRIENDS",
    }

    key = v.upper()
    return mapping.get(key, v)


def normalize_image_urls(raw_value):
    if raw_value is None:
        return []

    if isinstance(raw_value, str):
        raw_value = [raw_value]

    if not isinstance(raw_value, list):
        return []

    urls = []
    for item in raw_value:
        url = None

        if isinstance(item, str):
            url = item.strip()

        elif isinstance(item, dict):
            url = first_present(
                item.get("url"),
                item.get("image_url"),
                item.get("imageUrl"),
                default=""
            )
            if isinstance(url, str):
                url = url.strip()

        if url:
            urls.append(url)

    return urls


def safe_json(response):
    try:
        return response.json()
    except Exception:
        return {"raw": response.text}


async def refresh_access_token():
    tokens = load_tokens()
    if not tokens or not tokens.get("refresh_token"):
        return None, JSONResponse({"ok": False, "error": "No refresh_token. Visit /tiktok/login"}, status_code=400)

    client_key, err = require_env(TIKTOK_CLIENT_KEY, "TIKTOK_CLIENT_KEY")
    if err:
        return None, err

    client_secret, err = require_env(TIKTOK_CLIENT_SECRET, "TIKTOK_CLIENT_SECRET")
    if err:
        return None, err

    data = {
        "client_key": client_key,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        body = safe_json(r)

    if r.status_code != 200 or not body.get("access_token"):
        return None, JSONResponse({"ok": False, "token_response": body}, status_code=r.status_code)

    new_tokens = {
        **tokens,
        **body,
        "expires_at": time.time() + int(body.get("expires_in", 0)),
        "refresh_expires_at": time.time() + int(body.get("refresh_expires_in", 0))
    }
    save_tokens(new_tokens)
    return new_tokens, None


async def get_valid_access_token():
    tokens = load_tokens()
    if not tokens or not tokens.get("access_token"):
        return None, JSONResponse({"ok": False, "error": "Not authorized. Visit /tiktok/login"}, status_code=400)

    if token_expired(tokens):
        tokens, err = await refresh_access_token()
        if err:
            return None, err

    return tokens["access_token"], None


# ─────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────

@app.api_route("/health", methods=["GET", "HEAD"])
@app.api_route("/health/", methods=["GET", "HEAD"])
def health():
    return {"ok": True}


# ─────────────────────────────────────────────
# Serve post.html
# ─────────────────────────────────────────────

@app.get("/post")
@app.get("/post/")
def post_page():
    if os.path.exists("post.html"):
        return FileResponse("post.html", media_type="text/html")
    return HTMLResponse("<h2>post.html not found</h2>", status_code=404)


# ─────────────────────────────────────────────
# OAuth Flow
# ─────────────────────────────────────────────

@app.get("/tiktok/login")
@app.get("/tiktok/login/")
def tiktok_login():
    client_key, err = require_env(TIKTOK_CLIENT_KEY, "TIKTOK_CLIENT_KEY")
    if err:
        return err

    redirect_uri, err = require_env(TIKTOK_REDIRECT_URI, "TIKTOK_REDIRECT_URI")
    if err:
        return err

    state = secrets.token_urlsafe(16)
    params = {
        "client_key": client_key,
        "scope": DEFAULT_SCOPE,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state
    }
    auth_url = f"https://www.tiktok.com/v2/auth/authorize/?{urlencode(params)}"

    resp = RedirectResponse(auth_url, status_code=302)
    resp.set_cookie(
        key="tt_state",
        value=state,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=600
    )
    return resp


@app.api_route("/tiktok/callback", methods=["GET", "HEAD"])
@app.api_route("/tiktok/callback/", methods=["GET", "HEAD"])
async def tiktok_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    if request.method == "HEAD":
        return JSONResponse({"ok": True})

    if error:
        return JSONResponse(
            {"ok": False, "error": error, "error_description": error_description},
            status_code=400
        )

    if not code:
        return JSONResponse({"ok": False, "error": "Missing code"}, status_code=400)

    cookie_state = request.cookies.get("tt_state")
    if not cookie_state or state != cookie_state:
        return JSONResponse({"ok": False, "error": "Invalid state"}, status_code=400)

    cleanup_used_codes()
    if code in USED_CODES:
        return JSONResponse({"ok": False, "error": "Code already used"}, status_code=400)
    USED_CODES[code] = time.time()

    client_key, err = require_env(TIKTOK_CLIENT_KEY, "TIKTOK_CLIENT_KEY")
    if err:
        return err

    client_secret, err = require_env(TIKTOK_CLIENT_SECRET, "TIKTOK_CLIENT_SECRET")
    if err:
        return err

    redirect_uri, err = require_env(TIKTOK_REDIRECT_URI, "TIKTOK_REDIRECT_URI")
    if err:
        return err

    data = {
        "client_key": client_key,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        body = safe_json(r)

    if r.status_code == 200 and body.get("access_token"):
        stored = {
            **body,
            "expires_at": time.time() + int(body.get("expires_in", 0)),
            "refresh_expires_at": time.time() + int(body.get("refresh_expires_in", 0))
        }
        save_tokens(stored)
        resp = RedirectResponse("/post", status_code=302)
        resp.delete_cookie("tt_state")
        return resp

    return JSONResponse({"ok": False, "token_response": body}, status_code=r.status_code)


# ─────────────────────────────────────────────
# User & Token Info
# ─────────────────────────────────────────────

@app.get("/tiktok/userinfo")
@app.get("/tiktok/userinfo/")
async def tiktok_userinfo():
    access_token, err = await get_valid_access_token()
    if err:
        return err

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            "https://open.tiktokapis.com/v2/user/info/",
            params={"fields": "open_id,display_name,avatar_url"},
            headers={"Authorization": f"Bearer {access_token}"}
        )
        body = safe_json(r)

    user = body.get("data", {}).get("user", {})
    return {
        "ok": True,
        "open_id": user.get("open_id"),
        "display_name": user.get("display_name"),
        "avatar_url": user.get("avatar_url"),
    }


@app.get("/tiktok/token")
@app.get("/tiktok/token/")
def tiktok_token_info():
    t = load_tokens()
    if not t:
        return JSONResponse({"ok": False, "error": "No tokens stored yet"}, status_code=404)

    return {
        "ok": True,
        "open_id": t.get("open_id"),
        "scope": t.get("scope"),
        "expires_at": t.get("expires_at"),
        "refresh_expires_at": t.get("refresh_expires_at")
    }


# ─────────────────────────────────────────────
# Video Publishing (Form/UI)
# ─────────────────────────────────────────────

@app.post("/tiktok/publish_form")
@app.post("/tiktok/publish_form/")
async def tiktok_publish_form(
    video: UploadFile = File(...),
    title: str = Form("Posted via OuinOual"),
    privacylevel: str = Form("PUBLICTOEVERYONE"),
    disablecomment: str = Form("false"),
    disableduet: str = Form("false"),
    disablestitch: str = Form("false"),
    brandcontenttoggle: str = Form("false"),
    brandorganictoggle: str = Form("false"),
):
    access_token, err = await get_valid_access_token()
    if err:
        return err

    file_id = str(uuid.uuid4())
    filepath = os.path.join(FILES_DIR, f"{file_id}.mp4")

    with open(filepath, "wb") as f:
        f.write(await video.read())

    video_size = os.path.getsize(filepath)
    title = title.strip() or "Posted via OuinOual"
    privacy_level = normalize_privacy_level(privacylevel, default="PUBLICTOEVERYONE")

    init_body = {
        "post_info": {
            "title": title,
            "privacy_level": privacy_level,
            "disable_comment": to_bool(disablecomment),
            "disable_duet": to_bool(disableduet),
            "disable_stitch": to_bool(disablestitch),
            "brand_content_toggle": to_bool(brandcontenttoggle),
            "brand_organic_toggle": to_bool(brandorganictoggle),
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": video_size,
            "total_chunk_count": 1,
        }
    }

    async with httpx.AsyncClient(timeout=60) as client:
        init_r = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/video/init/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8"
            },
            json=init_body
        )
        init_json = safe_json(init_r)

    data = init_json.get("data", {})
    if init_r.status_code != 200 or not data.get("upload_url"):
        return JSONResponse({"ok": False, "step": "init", "response": init_json}, status_code=init_r.status_code)

    publish_id = data["publish_id"]
    upload_url = data["upload_url"]

    with open(filepath, "rb") as f:
        video_bytes = f.read()

    put_headers = {
        "Content-Type": "video/mp4",
        "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
        "Content-Length": str(video_size),
    }

    async with httpx.AsyncClient(timeout=None) as client:
        put_r = await client.put(upload_url, content=video_bytes, headers=put_headers)

    if put_r.status_code not in (200, 201, 204):
        return JSONResponse({"ok": False, "step": "upload", "status_code": put_r.status_code}, status_code=400)

    async with httpx.AsyncClient(timeout=30) as client:
        status_r = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8"
            },
            json={"publish_id": publish_id}
        )

    return {
        "ok": True,
        "publish_id": publish_id,
        "upload_http_status": put_r.status_code,
        "status": safe_json(status_r)
    }


# ─────────────────────────────────────────────
# Video Publishing (API Payload)
# ─────────────────────────────────────────────

@app.post("/tiktok/publish")
@app.post("/tiktok/publish/")
async def tiktok_publish(payload: dict):
    access_token, err = await get_valid_access_token()
    if err:
        return err

    file_id = first_present(payload.get("file_id"), payload.get("fileId"))
    filepath = first_present(payload.get("filepath"), payload.get("filePath"))
    title = str(first_present(payload.get("title"), default="Posted via API")).strip() or "Posted via API"

    privacy_raw = first_present(
        payload.get("privacy_level"),
        payload.get("privacyLevel"),
        payload.get("privacylevel"),
        default="SELFONLY"
    )
    privacy_level = normalize_privacy_level(privacy_raw, default="SELFONLY")

    if file_id:
        filepath = os.path.join(FILES_DIR, f"{file_id}.mp4")

    if not filepath or not os.path.exists(filepath):
        return JSONResponse({"ok": False, "error": "Missing file"}, status_code=400)

    video_size = os.path.getsize(filepath)

    allow_comment = first_present(payload.get("allow_comment"), payload.get("allowcomment"))
    allow_duet = first_present(payload.get("allow_duet"), payload.get("allowduet"))
    allow_stitch = first_present(payload.get("allow_stitch"), payload.get("allowstitch"))

    disable_comment = first_present(
        payload.get("disable_comment"),
        payload.get("disablecomment"),
        default=(not to_bool(allow_comment, default=False))
    )
    disable_duet = first_present(
        payload.get("disable_duet"),
        payload.get("disableduet"),
        default=(not to_bool(allow_duet, default=False))
    )
    disable_stitch = first_present(
        payload.get("disable_stitch"),
        payload.get("disablestitch"),
        default=(not to_bool(allow_stitch, default=False))
    )

    init_body = {
        "post_info": {
            "title": title,
            "privacy_level": privacy_level,
            "disable_comment": to_bool(disable_comment),
            "disable_duet": to_bool(disable_duet),
            "disable_stitch": to_bool(disable_stitch),
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": video_size,
            "total_chunk_count": 1,
        }
    }

    async with httpx.AsyncClient(timeout=60) as client:
        init_r = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/video/init/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8"
            },
            json=init_body
        )
        init_json = safe_json(init_r)

    data = init_json.get("data", {})
    if init_r.status_code != 200 or not data.get("upload_url"):
        return JSONResponse({"ok": False, "step": "init", "response": init_json}, status_code=init_r.status_code)

    publish_id = data["publish_id"]
    upload_url = data["upload_url"]

    with open(filepath, "rb") as f:
        video_bytes = f.read()

    put_headers = {
        "Content-Type": "video/mp4",
        "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
        "Content-Length": str(video_size),
    }

    async with httpx.AsyncClient(timeout=None) as client:
        put_r = await client.put(upload_url, content=video_bytes, headers=put_headers)

    if put_r.status_code not in (200, 201, 204):
        return JSONResponse({"ok": False, "step": "upload", "status_code": put_r.status_code}, status_code=400)

    async with httpx.AsyncClient(timeout=30) as client:
        status_r = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8"
            },
            json={"publish_id": publish_id}
        )

    return {
        "ok": True,
        "publish_id": publish_id,
        "init": init_json,
        "upload_http_status": put_r.status_code,
        "status": safe_json(status_r)
    }


# ─────────────────────────────────────────────
# Status Check
# ─────────────────────────────────────────────

@app.post("/tiktok/status")
@app.post("/tiktok/status/")
async def tiktok_status(payload: dict):
    access_token, err = await get_valid_access_token()
    if err:
        return err

    publish_id = payload.get("publish_id")
    if not publish_id:
        return JSONResponse({"ok": False, "error": "Missing publish_id"}, status_code=400)

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8"
            },
            json={"publish_id": publish_id}
        )

    return JSONResponse({"ok": True, "response": safe_json(r)}, status_code=r.status_code)


# ─────────────────────────────────────────────
# Extraction Tool
# ─────────────────────────────────────────────

@app.post("/extract")
@app.post("/extract/")
def extract(payload: dict):
    url = payload.get("url")
    if not url:
        return JSONResponse({"ok": False, "error": "Missing url"}, status_code=400)

    file_id = str(uuid.uuid4())
    out_tmpl = os.path.join(FILES_DIR, f"{file_id}.%(ext)s")

    subprocess.check_call([
        "yt-dlp",
        "-f", "bv+ba/best",
        "--merge-output-format", "mp4",
        "-o", out_tmpl,
        url
    ])

    if not PUBLIC_BASE_URL:
        return JSONResponse({"ok": False, "error": "Missing PUBLIC_BASE_URL"}, status_code=500)

    return {
        "ok": True,
        "file_id": file_id,
        "fileUrl": f"{PUBLIC_BASE_URL}/files/{file_id}.mp4"
    }


# ─────────────────────────────────────────────
# Photo Publishing (API Payload)
# ─────────────────────────────────────────────

@app.post("/tiktok/publish_photo")
@app.post("/tiktok/publish_photo/")
async def tiktok_publish_photo(payload: dict):
    access_token, err = await get_valid_access_token()
    if err:
        return err

    post_info = payload.get("post_info") or {}
    source_info = payload.get("source_info") or {}

    image_urls = normalize_image_urls(
        first_present(
            payload.get("image_urls"),
            payload.get("image_url"),
            source_info.get("photo_images"),
            default=[]
        )
    )

    if not image_urls:
        return JSONResponse({"ok": False, "error": "Missing image_urls"}, status_code=400)

    title = str(first_present(
        payload.get("title"),
        post_info.get("title"),
        default="Check out this deal!"
    )).strip() or "Check out this deal!"

    description = str(first_present(
        payload.get("description"),
        post_info.get("description"),
        default=""
    ))

    privacy_raw = first_present(
        payload.get("privacy_level"),
        payload.get("privacylevel"),
        post_info.get("privacy_level"),
        post_info.get("privacylevel"),
        default="SELFONLY"
    )
    privacy_level = normalize_privacy_level(privacy_raw, default="SELFONLY")

    disable_comment = to_bool(first_present(
        payload.get("disable_comment"),
        payload.get("disablecomment"),
        post_info.get("disable_comment"),
        post_info.get("disablecomment"),
        default=False
    ))

    auto_add_music = to_bool(first_present(
        payload.get("auto_add_music"),
        payload.get("autoaddmusic"),
        post_info.get("auto_add_music"),
        post_info.get("autoaddmusic"),
        default=True
    ))

    source_value = str(first_present(
        source_info.get("source"),
        payload.get("source"),
        default="PULL_FROM_URL"
    ))

    photo_cover_index = first_present(
        source_info.get("photo_cover_index"),
        source_info.get("photocoverindex"),
        payload.get("photo_cover_index"),
        payload.get("photocoverindex"),
        default=0
    )
    try:
        photo_cover_index = int(photo_cover_index)
    except Exception:
        photo_cover_index = 0

    post_mode = str(first_present(
        payload.get("post_mode"),
        payload.get("postmode"),
        default="DIRECT_POST"
    ))

    media_type = str(first_present(
        payload.get("media_type"),
        payload.get("mediatype"),
        default="PHOTO"
    ))

    init_body = {
        "post_info": {
            "title": title,
            "description": description,
            "privacy_level": privacy_level,
            "disable_comment": disable_comment,
            "auto_add_music": auto_add_music
        },
        "source_info": {
            "source": source_value,
            "photo_cover_index": photo_cover_index,
            "photo_images": image_urls[:35]
        },
        "post_mode": post_mode,
        "media_type": media_type
    }

    async with httpx.AsyncClient(timeout=60) as client:
        init_r = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/content/init/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8"
            },
            json=init_body
        )
        init_json = safe_json(init_r)

    data = init_json.get("data", {})
    if init_r.status_code != 200 or not data.get("publish_id"):
        return JSONResponse({"ok": False, "step": "init", "response": init_json}, status_code=init_r.status_code)

    publish_id = data["publish_id"]

    async with httpx.AsyncClient(timeout=30) as client:
        status_r = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8"
            },
            json={"publish_id": publish_id}
        )

    return {
        "ok": True,
        "publish_id": publish_id,
        "init": init_json,
        "status": safe_json(status_r)
    }
