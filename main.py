import os
import json
import time
import uuid
import secrets
import subprocess
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

try:
    from tracker import (
        track_publish,
        sync_metrics_for_post,
        run_sync_all,
        get_post,
        get_all_posts,
    )
except Exception:
    track_publish = None
    sync_metrics_for_post = None
    run_sync_all = None
    get_post = None
    get_all_posts = None

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


def require_env(value: Optional[str], name: str):
    if not value:
        return None, JSONResponse(
            {"ok": False, "error": f"Missing environment variable: {name}"},
            status_code=500,
        )
    return value, None


def load_tokens():
    if not os.path.exists(TOKENS_PATH):
        return None
    with open(TOKENS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_tokens(tokens: dict):
    with open(TOKENS_PATH, "w", encoding="utf-8") as f:
        json.dump(tokens, f, ensure_ascii=False, indent=2)


def cleanup_used_codes():
    now = time.time()
    expired = [k for k, ts in USED_CODES.items() if (now - ts) > USED_CODES_TTL_SECONDS]
    for k in expired:
        USED_CODES.pop(k, None)


def token_expired(tokens: dict) -> bool:
    return time.time() >= float(tokens.get("expires_at", 0)) - TOKEN_SKEW_SECONDS


def safe_json(response):
    try:
        return response.json()
    except Exception:
        return {"raw": response.text}


def normalize_privacy(value: Optional[str], default: str = "SELF_ONLY") -> str:
    v = (value or default).strip().upper()
    mapping = {
        "PRIVATE": "SELF_ONLY",
        "SELF_ONLY": "SELF_ONLY",
        "ONLY_ME": "SELF_ONLY",
        "PUBLIC": "PUBLIC_TO_EVERYONE",
        "PUBLIC_TO_EVERYONE": "PUBLIC_TO_EVERYONE",
        "FRIENDS": "MUTUAL_FOLLOW_FRIENDS",
        "MUTUAL_FOLLOW_FRIENDS": "MUTUAL_FOLLOW_FRIENDS",
        "FOLLOWERS": "FOLLOWER_OF_CREATOR",
        "FOLLOWER_OF_CREATOR": "FOLLOWER_OF_CREATOR",
    }
    return mapping.get(v, v)


@app.get("/debug-tokens")
def debug_tokens():
    try:
        t = load_tokens()
        if not t:
            return JSONResponse({"ok": False, "error": "No tokens file"}, status_code=404)
        safe_t = {
            k: "***HIDDEN***" if k in ["access_token", "refresh_token"] else v
            for k, v in t.items()
        }
        return JSONResponse({
            "ok": True,
            "path": os.path.abspath(TOKENS_PATH),
            "tokens": safe_t,
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def refresh_access_token():
    tokens = load_tokens()
    if not tokens or not tokens.get("refresh_token"):
        return None, JSONResponse(
            {"ok": False, "error": "No refresh_token stored. Visit /tiktok/login"},
            status_code=400,
        )

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
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    body = safe_json(r)
    if r.status_code != 200 or not body.get("access_token"):
        return None, JSONResponse({"ok": False, "token_response": body}, status_code=r.status_code)

    new_tokens = {
        **tokens,
        **body,
        "expires_at": time.time() + int(body.get("expires_in", 0)),
        "refresh_expires_at": time.time() + int(body.get("refresh_expires_in", 0)),
    }
    save_tokens(new_tokens)
    return new_tokens, None


async def get_valid_access_token():
    tokens = load_tokens()
    if not tokens or not tokens.get("access_token"):
        return None, JSONResponse(
            {"ok": False, "error": "Not authorized yet. Visit /tiktok/login"},
            status_code=400,
        )
    if token_expired(tokens):
        tokens, err = await refresh_access_token()
        if err:
            return None, err
    return tokens["access_token"], None


@app.get("/health")
@app.get("/health/")
def health():
    return {"ok": True}


@app.get("/post")
@app.get("/post/")
def post_page():
    if os.path.exists("post.html"):
        return FileResponse("post.html", media_type="text/html")
    return HTMLResponse("<h1>post.html not found</h1>", status_code=404)


@app.post("/extract")
@app.post("/extract/")
def extract(payload: dict):
    url = payload.get("url")
    if not url:
        return JSONResponse({"ok": False, "error": "Missing url"}, status_code=400)

    file_id = str(uuid.uuid4())
    outtmpl = os.path.join(FILES_DIR, f"{file_id}.%(ext)s")
    cmd = ["yt-dlp", "-f", "bv*+ba/best", "--merge-output-format", "mp4", "-o", outtmpl, url]
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as e:
        return JSONResponse({"ok": False, "error": "extract_failed", "detail": str(e)}, status_code=400)

    final_path = os.path.join(FILES_DIR, f"{file_id}.mp4")
    if not os.path.exists(final_path):
        return JSONResponse({"ok": False, "error": "output_not_found"}, status_code=500)
    if not PUBLIC_BASE_URL:
        return JSONResponse({"ok": False, "error": "Missing PUBLIC_BASE_URL"}, status_code=500)

    return {
        "ok": True,
        "file_id": file_id,
        "fileId": file_id,
        "file_url": f"{PUBLIC_BASE_URL}/files/{file_id}.mp4",
        "fileUrl": f"{PUBLIC_BASE_URL}/files/{file_id}.mp4",
    }


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
        "state": state,
    }
    auth_url = "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(params)
    resp = RedirectResponse(auth_url, status_code=302)
    resp.set_cookie("tt_state", state, httponly=True, secure=True, samesite="lax", max_age=10 * 60)
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
            {"ok": False, "error": error, "error_description": error_description, "state": state},
            status_code=400,
        )
    if not code:
        return JSONResponse({"ok": False, "error": "Missing code", "state": state}, status_code=400)

    cookie_state = request.cookies.get("tt_state")
    if not cookie_state or state != cookie_state:
        return JSONResponse({"ok": False, "error": "Invalid state", "state": state}, status_code=400)

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
        "redirect_uri": redirect_uri,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    body = safe_json(r)
    if r.status_code == 200 and body.get("access_token"):
        stored = {
            **body,
            "expires_at": time.time() + int(body.get("expires_in", 0)),
            "refresh_expires_at": time.time() + int(body.get("refresh_expires_in", 0)),
        }
        save_tokens(stored)

    resp = JSONResponse({"ok": True, "state": state, "token_response": body, "status_code": r.status_code})
    resp.delete_cookie("tt_state")
    return resp


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
        "refresh_expires_at": t.get("refresh_expires_at"),
    }


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
            headers={"Authorization": f"Bearer {access_token}"},
        )

    body = safe_json(r)
    user = body.get("data", {}).get("user", {})
    return JSONResponse(
        {
            "ok": True,
            "open_id": user.get("open_id"),
            "display_name": user.get("display_name"),
            "avatar_url": user.get("avatar_url"),
        }
    )


@app.post("/tiktok/publish")
@app.post("/tiktok/publish/")
async def tiktok_publish(payload: dict):
    access_token, err = await get_valid_access_token()
    if err:
        return err

    file_id = payload.get("file_id") or payload.get("fileId")
    filepath = payload.get("filepath") or payload.get("filePath")
    if file_id and not filepath:
        filepath = os.path.join(FILES_DIR, f"{file_id}.mp4")
    if not filepath or not os.path.exists(filepath):
        return JSONResponse(
            {"ok": False, "error": "Missing filepath or file not found", "file_id": file_id, "filepath": filepath},
            status_code=400,
        )

    title = payload.get("title", "Posted via API")
    privacy_level = normalize_privacy(payload.get("privacy_level"), "SELF_ONLY")
    video_size = os.path.getsize(filepath)

    init_body = {
        "post_info": {
            "title": title,
            "privacy_level": privacy_level,
            "disable_comment": bool(payload.get("disable_comment", False)),
            "disable_duet": bool(payload.get("disable_duet", False)),
            "disable_stitch": bool(payload.get("disable_stitch", False)),
            "brand_content_toggle": bool(payload.get("brand_content_toggle", False)),
            "brand_organic_toggle": bool(payload.get("brand_organic_toggle", False)),
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": video_size,
            "total_chunk_count": 1,
        },
    }

    async with httpx.AsyncClient(timeout=60) as client:
        init_r = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/video/init/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json=init_body,
        )

    init_json = safe_json(init_r)
    data = init_json.get("data") or {}
    if init_r.status_code != 200 or not data.get("upload_url") or not data.get("publish_id"):
        return JSONResponse({"ok": False, "step": "init", "response": init_json, "status_code": init_r.status_code}, status_code=400)

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
        return JSONResponse({"ok": False, "step": "upload", "status_code": put_r.status_code, "text": put_r.text[:1000]}, status_code=400)

    async with httpx.AsyncClient(timeout=30) as client:
        status_r = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
        )

    return {"ok": True, "publish_id": publish_id, "init": init_json, "upload_http_status": put_r.status_code, "status": safe_json(status_r)}


@app.post("/tiktok/publish_photo")
@app.post("/tiktok/publish_photo/")
async def tiktok_publish_photo(payload: dict):
    access_token, err = await get_valid_access_token()
    if err:
        return err

    if payload.get("post_info") or payload.get("source_info"):
        post_info = payload.get("post_info") or {}
        source_info = payload.get("source_info") or {}
        title = post_info.get("title", "")
        description = post_info.get("description", "")
        privacy_level = normalize_privacy(post_info.get("privacy_level"), "SELF_ONLY")
        photo_images = source_info.get("photo_images") or []
        photo_cover_index = source_info.get("photo_cover_index", 0)
        post_mode = payload.get("post_mode", "DIRECT_POST")
    else:
        photo_url = payload.get("photo_url") or payload.get("url")
        if not photo_url:
            return JSONResponse({"ok": False, "error": "Missing photo_url"}, status_code=400)
        title = payload.get("title", "")
        description = payload.get("description", "")
        privacy_level = normalize_privacy(payload.get("privacy_level"), "SELF_ONLY")
        photo_images = [photo_url]
        photo_cover_index = 0
        post_mode = payload.get("post_mode", "DIRECT_POST")

    if not photo_images:
        return JSONResponse({"ok": False, "error": "Missing photo_images"}, status_code=400)

    init_body = {
        "media_type": "PHOTO",
        "post_mode": post_mode,
        "post_info": {
            "title": title,
            "description": description,
            "privacy_level": privacy_level,
            "disable_comment": bool((payload.get("post_info") or {}).get("disable_comment", payload.get("disable_comment", False))),
            "auto_add_music": bool((payload.get("post_info") or {}).get("auto_add_music", payload.get("auto_add_music", False))),
        },
        "source_info": {
            "source": "PULL_FROM_URL",
            "photo_cover_index": photo_cover_index,
            "photo_images": photo_images,
        },
    }

    async with httpx.AsyncClient(timeout=60) as client:
        init_r = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/content/init/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json=init_body,
        )

    init_json = safe_json(init_r)
    data = init_json.get("data") or {}
    if init_r.status_code != 200 or not data.get("publish_id"):
        return JSONResponse({"ok": False, "step": "init", "response": init_json, "status_code": init_r.status_code}, status_code=400)

    publish_id = data["publish_id"]
    async with httpx.AsyncClient(timeout=30) as client:
        status_r = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
        )

    return {"ok": True, "publish_id": publish_id, "init": init_json, "status": safe_json(status_r)}


@app.post("/tiktok/status")
@app.post("/tiktok/status/")
async def tiktok_status(payload: dict):
    access_token, err = await get_valid_access_token()
    if err:
        return err

    publish_id = payload.get("publish_id") or payload.get("publishId")
    if not publish_id:
        return JSONResponse({"ok": False, "error": "Missing publish_id"}, status_code=400)

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
        )

    return JSONResponse({"ok": True, "response": safe_json(r), "status_code": r.status_code})


@app.post("/track-publish")
async def track_publish_endpoint(request: Request):
    if track_publish is None:
        return JSONResponse({"ok": False, "error": "tracker_not_available"}, status_code=501)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)
    try:
        result = await track_publish(body)
        return JSONResponse({"ok": True, "saved": result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/sync-metrics")
async def sync_metrics_endpoint(request: Request):
    if sync_metrics_for_post is None:
        return JSONResponse({"ok": False, "error": "tracker_not_available"}, status_code=501)
    try:
        body = await request.json()
    except Exception:
        body = {}
    platform = (body.get("platform") or "").strip().lower()
    platform_post_id = (body.get("platform_post_id") or "").strip()
    if not platform or not platform_post_id:
        return JSONResponse({"ok": False, "error": "platform and platform_post_id are required"}, status_code=400)
    try:
        synced = await sync_metrics_for_post(platform, platform_post_id)
        if synced:
            return JSONResponse({"ok": True, "updated": 1, "metrics": synced.get("metrics", {}), "record": synced})
        return JSONResponse({"ok": False, "error": "post_not_found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/metrics/{platform}/{platform_post_id}")
async def get_metrics(platform: str, platform_post_id: str):
    if get_post is None:
        return JSONResponse({"ok": False, "error": "tracker_not_available"}, status_code=501)
    post = get_post(platform, platform_post_id)
    if not post:
        return JSONResponse({"ok": False, "error": "post_not_found"}, status_code=404)
    return JSONResponse({"ok": True, "record": post})


@app.get("/metrics/all")
async def get_all_metrics():
    if run_sync_all is None or get_all_posts is None:
        return JSONResponse({"ok": False, "error": "tracker_not_available"}, status_code=501)
    count = await run_sync_all()
    posts = get_all_posts()
    return JSONResponse({"ok": True, "updated_count": count, "count": len(posts), "records": posts})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))