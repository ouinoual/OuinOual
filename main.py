import os
import json
import time
import uuid
import secrets
import subprocess
from typing import Optional, Tuple
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# منع 307 بين /path و /path/ لتفادي callback مزدوج
app = FastAPI(redirect_slashes=False)

FILES_DIR = "files"
os.makedirs(FILES_DIR, exist_ok=True)
app.mount("/files", StaticFiles(directory=FILES_DIR), name="files")

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL")

TIKTOK_CLIENT_KEY = os.environ.get("TIKTOK_CLIENT_KEY")
TIKTOK_CLIENT_SECRET = os.environ.get("TIKTOK_CLIENT_SECRET")
TIKTOK_REDIRECT_URI = os.environ.get("TIKTOK_REDIRECT_URI")

# طلب الصلاحيات المطلوبة (ومنها video.publish)
DEFAULT_SCOPE = "user.info.basic,video.upload,video.publish"

TOKENS_PATH = os.environ.get("TOKENS_PATH", "tokens.json")
TOKEN_SKEW_SECONDS = 120  # جدد قبل الانتهاء بـ 120 ثانية

USED_CODES = {}
USED_CODES_TTL_SECONDS = 10 * 60  # 10 دقائق


def require_env(value: Optional[str], name: str) -> Tuple[Optional[str], Optional[JSONResponse]]:
    if not value:
        return None, JSONResponse(
            {"ok": False, "error": f"Missing environment variable: {name}"},
            status_code=500,
        )
    return value, None


def load_tokens() -> Optional[dict]:
    if not os.path.exists(TOKENS_PATH):
        return None
    with open(TOKENS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_tokens(tokens: dict) -> None:
    with open(TOKENS_PATH, "w", encoding="utf-8") as f:
        json.dump(tokens, f, ensure_ascii=False, indent=2)


def cleanup_used_codes() -> None:
    now = time.time()
    expired = [k for k, ts in USED_CODES.items() if (now - ts) > USED_CODES_TTL_SECONDS]
    for k in expired:
        USED_CODES.pop(k, None)


def token_expired(tokens: dict) -> bool:
    return time.time() >= float(tokens.get("expires_at", 0)) - TOKEN_SKEW_SECONDS


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

    body = r.json()
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


@app.post("/extract")
@app.post("/extract/")
def extract(payload: dict):
    url = payload.get("url")
    if not url:
        return JSONResponse({"ok": False, "error": "Missing url"}, status_code=400)

    file_id = str(uuid.uuid4())
    outtmpl = os.path.join(FILES_DIR, f"{file_id}.%(ext)s")

    cmd = [
        "yt-dlp",
        "-f", "bv*+ba/best",
        "--merge-output-format", "mp4",
        "-o", outtmpl,
        url,
    ]
    subprocess.check_call(cmd)

    if not PUBLIC_BASE_URL:
        return JSONResponse({"ok": False, "error": "Missing PUBLIC_BASE_URL"}, status_code=500)

    return {"ok": True, "file_id": file_id, "fileUrl": f"{PUBLIC_BASE_URL}/files/{file_id}.mp4"}


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
    resp.set_cookie(
        key="tt_state",
        value=state,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=10 * 60,
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
    # بعض الوسطاء يرسلون HEAD
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

    body = r.json()
    if r.status_code == 200 and body.get("access_token"):
        stored = {
            **body,
            "expires_at": time.time() + int(body.get("expires_in", 0)),
            "refresh_expires_at": time.time() + int(body.get("refresh_expires_in", 0)),
        }
        save_tokens(stored)

    resp = JSONResponse({"ok": r.status_code == 200, "state": state, "token_response": body}, status_code=r.status_code)
    resp.delete_cookie("tt_state")
    return resp


@app.get("/tiktok/token")
@app.get("/tiktok/token/")
def tiktok_token_info():
    t = load_tokens()
    if not t:
        return JSONResponse({"ok": False, "error": "No tokens stored yet"}, status_code=404)

    # لا نُظهر access_token / refresh_token
    return {
        "ok": True,
        "open_id": t.get("open_id"),
        "scope": t.get("scope"),
        "expires_at": t.get("expires_at"),
        "refresh_expires_at": t.get("refresh_expires_at"),
    }


@app.post("/tiktok/publish")
@app.post("/tiktok/publish/")
async def tiktok_publish(payload: dict):
    """
    Direct Post: /v2/post/publish/video/init/ ثم PUT upload_url ثم status/fetch
    """
    access_token, err = await get_valid_access_token()
    if err:
        return err

    # دعم أسماء مختلفة حتى لا تتعطل السيناريوهات:
    # fileid / file_id
    # filepath / file_path
    file_id = payload.get("fileid") or payload.get("file_id")
    file_path = payload.get("filepath") or payload.get("file_path")

    # إصلاح دائم: تنظيف النصوص + قيمة افتراضية إذا صار العنوان فارغًا
    title = (payload.get("title") or "").strip()
    if not title:
        title = "Posted via API"

    # دعم privacy_level أو privacylevel + تنظيف المسافات
    privacy_level = (payload.get("privacy_level") or payload.get("privacylevel") or "PRIVATE").strip()

    if file_id:
        file_path = os.path.join(FILES_DIR, f"{file_id}.mp4")

    if not file_path or not os.path.exists(file_path):
        return JSONResponse({"ok": False, "error": "Missing file_path or file not found"}, status_code=400)

    video_size = os.path.getsize(file_path)

    init_body = {
        "post_info": {
            "title": title,
            "privacy_level": privacy_level,
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

    init_json = init_r.json()
    data = init_json.get("data") or {}

    if init_r.status_code != 200 or not data.get("upload_url") or not data.get("publish_id"):
        return JSONResponse({"ok": False, "step": "init", "response": init_json}, status_code=init_r.status_code)

    publish_id = data["publish_id"]
    upload_url = data["upload_url"]

    # رفع الفيديو بـ PUT إلى upload_url (Chunk واحد)
    start = 0
    end = video_size - 1
    put_headers = {
        "Content-Type": "video/mp4",
        "Content-Range": f"bytes {start}-{end}/{video_size}",
        "Content-Length": str(video_size),
    }

    # FIX: لا تمرّر file-handle إلى AsyncClient (يسبب sync/async mismatch)
    with open(file_path, "rb") as f:
        video_bytes = f.read()

    async with httpx.AsyncClient(timeout=None) as client:
        put_r = await client.put(upload_url, content=video_bytes, headers=put_headers)

    if put_r.status_code not in (200, 201, 204):
        return JSONResponse(
            {"ok": False, "step": "upload", "status_code": put_r.status_code, "text": put_r.text},
            status_code=400,
        )

    async with httpx.AsyncClient(timeout=30) as client:
        status_r = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
        )

    return {
        "ok": True,
        "publish_id": publish_id,
        "init": init_json,
        "upload_http_status": put_r.status_code,
        "status": status_r.json(),
    }


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
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
        )

    return JSONResponse({"ok": True, "response": r.json()}, status_code=r.status_code)
    
@app.post("/tiktok/publish_photo")
@app.post("/tiktok/publish_photo/")
async def tiktok_publish_photo(payload: dict):
    """
    نشر صورة (أو عدة صور) على TikTok عبر Content API
    يستخدم post/publish/content/init مع source=PULL_FROM_URL
    """
    access_token, err = await get_valid_access_token()
    if err:
        return err

    # دعم أسماء مختلفة للتوافق مع Make
    image_urls = payload.get("image_urls") or payload.get("image_url") or []
    if isinstance(image_urls, str):
        image_urls = [image_urls]
    
    if not image_urls:
        return JSONResponse(
            {"ok": False, "error": "Missing image_urls"}, 
            status_code=400
        )
    
    # تنظيف العنوان
    title = (payload.get("title") or "").strip()
    if not title:
        title = "Check out this deal!"
    
    # خصوصية المنشور
    privacy_level = (payload.get("privacy_level") or payload.get("privacylevel") or "PUBLIC").strip()

    # TikTok يدعم حتى 35 صورة
    photo_images = [{"url": url} for url in image_urls[:35]]

    init_body = {
        "post_info": {
            "title": title,
            "privacy_level": privacy_level,
        },
        "source_info": {
            "source": "PULL_FROM_URL",
            "photo_cover_index": 0,
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

    init_json = init_r.json()
    data = init_json.get("data") or {}

    if init_r.status_code != 200 or not data.get("publish_id"):
        return JSONResponse(
            {"ok": False, "step": "init", "response": init_json},
            status_code=init_r.status_code
        )

    publish_id = data["publish_id"]
    
    # التحقق من حالة النشر
    async with httpx.AsyncClient(timeout=30) as client:
        status_r = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
        )

    return {
        "ok": True,
        "publish_id": publish_id,
        "init": init_json,
        "status": status_r.json(),
    }
@app.post("/publish_video_deal")
@app.post("/publish_video_deal/")
async def publish_video_deal(payload: dict):
    import video_generator
    import uuid
    
    deal = payload.get("deal", {})
    title = payload.get("title", "Deal!")
    privacy = payload.get("privacy_level", "PRIVATE")
    
    # توليد الفيديو
    file_id = str(uuid.uuid4())
    video_path = f"files/{file_id}.mp4"
    
    result = video_generator.create_video_from_deal(deal, video_path)
    if not result:
        return JSONResponse({"ok": False, "error": "فشل توليد الفيديو"}, status_code=500)
    
    # نشر على تيك توك
    access_token, err = await get_valid_access_token()
    if err:
        return err
    
    video_size = os.path.getsize(video_path)
    
    init_body = {
        "post_info": {"title": title, "privacy_level": privacy},
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
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=UTF-8"},
            json=init_body,
        )
    
    data = init_r.json().get("data", {})
    if not data.get("upload_url"):
        return JSONResponse({"ok": False, "response": init_r.json()}, status_code=400)
    
    publish_id = data["publish_id"]
    upload_url = data["upload_url"]
    
    with open(video_path, "rb") as f:
        video_bytes = f.read()
    
    headers = {
        "Content-Type": "video/mp4",
        "Content-Range": f"bytes 0-{video_size-1}/{video_size}",
        "Content-Length": str(video_size),
    }
    
    async with httpx.AsyncClient(timeout=None) as client:
        put_r = await client.put(upload_url, content=video_bytes, headers=headers)
    
    return {"ok": True, "publish_id": publish_id, "status": "تم الرفع"}
