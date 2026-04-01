import os
import json
import time
import uuid
import secrets
from urllib.parse import urlencode
import httpx
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from tracker import (
    track_publish,
    sync_metrics_for_post,
    run_sync_all,
    get_post,
    get_all_posts,
)

app = FastAPI(redirect_slashes=False)
FILES_DIR = "files"
os.makedirs(FILES_DIR, exist_ok=True)
app.mount("/files", StaticFiles(directory=FILES_DIR), name="files")

PUBLIC_BASE_URL       = os.environ.get("PUBLIC_BASE_URL")
TIKTOK_CLIENT_KEY     = os.environ.get("TIKTOK_CLIENT_KEY")
TIKTOK_CLIENT_SECRET  = os.environ.get("TIKTOK_CLIENT_SECRET")
TIKTOK_REDIRECT_URI   = os.environ.get("TIKTOK_REDIRECT_URI")
DEFAULT_SCOPE         = "user.info.basic,video.upload,video.publish"
TOKENS_PATH           = os.environ.get("TOKENS_PATH", "tokens.json")
TOKEN_SKEW_SECONDS    = 120
USED_CODES            = {}
USED_CODES_TTL_SECONDS = 10 * 60

# ─── Helpers ─────────────────────────────────────────────────────────────

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

def safe_json(response):
    try:
        return response.json()
    except Exception:
        return {"raw": response.text}

# ─── TikTok Auth ─────────────────────────────────────────────────────────────

async def refresh_access_token():
    tokens = load_tokens()
    if not tokens or not tokens.get("refresh_token"):
        return None, JSONResponse({"ok": False, "error": "No refresh_token. Visit /tiktok/login"}, status_code=400)
    client_key, err = require_env(TIKTOK_CLIENT_KEY, "TIKTOK_CLIENT_KEY")
    if err: return None, err
    client_secret, err = require_env(TIKTOK_CLIENT_SECRET, "TIKTOK_CLIENT_SECRET")
    if err: return None, err
    data = {
        "client_key":    client_key,
        "client_secret": client_secret,
        "grant_type":    "refresh_token",
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
        **tokens, **body,
        "expires_at":         time.time() + int(body.get("expires_in", 0)),
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
        if err: return None, err
    return tokens["access_token"], None

# ─── Health ──────────────────────────────────────────────────────────────────

@app.api_route("/health", methods=["GET", "HEAD"])
@app.api_route("/health/", methods=["GET", "HEAD"])
def health():
    return {"ok": True}

@app.get("/post")
@app.get("/post/")
def post_page():
    if os.path.exists("post.html"):
        return FileResponse("post.html", media_type="text/html")
    return HTMLResponse("<h1>post.html not found</h1>", status_code=404)

# ─── TikTok Login ─────────────────────────────────────────────────────────────

@app.get("/tiktok/login")
@app.get("/tiktok/login/")
async def tiktok_login(request: Request):
    client_key, err = require_env(TIKTOK_CLIENT_KEY, "TIKTOK_CLIENT_KEY")
    if err: return err
    redirect_uri, err = require_env(TIKTOK_REDIRECT_URI, "TIKTOK_REDIRECT_URI")
    if err: return err
    state = secrets.token_urlsafe(16)
    params = {
        "client_key":    client_key,
        "scope":         request.query_params.get("scope", DEFAULT_SCOPE),
        "response_type": "code",
        "redirect_uri":  redirect_uri,
        "state":         state,
    }
    return RedirectResponse("https://www.tiktok.com/v2/auth/authorize/?" + urlencode(params))

@app.get("/tiktok/callback")
@app.get("/tiktok/callback/")
async def tiktok_callback(request: Request):
    cleanup_used_codes()
    code  = request.query_params.get("code", "").strip()
    error = request.query_params.get("error", "").strip()
    if error:
        return JSONResponse({"ok": False, "error": error})
    if not code:
        return JSONResponse({"ok": False, "error": "Missing code"}, status_code=400)
    if code in USED_CODES:
        return JSONResponse({"ok": False, "error": "Code already used"}, status_code=400)
    USED_CODES[code] = time.time()
    client_key,    err = require_env(TIKTOK_CLIENT_KEY,    "TIKTOK_CLIENT_KEY");    
    if err: return err
    client_secret, err = require_env(TIKTOK_CLIENT_SECRET, "TIKTOK_CLIENT_SECRET"); 
    if err: return err
    redirect_uri,  err = require_env(TIKTOK_REDIRECT_URI,  "TIKTOK_REDIRECT_URI");  
    if err: return err
    data = {
        "client_key":    client_key,
        "client_secret": client_secret,
        "code":          code,
        "grant_type":    "authorization_code",
        "redirect_uri":  redirect_uri,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
    body = safe_json(r)
    if r.status_code != 200 or not body.get("access_token"):
        return JSONResponse({"ok": False, "token_response": body}, status_code=r.status_code)
    save_tokens({
        **body,
        "expires_at":         time.time() + int(body.get("expires_in", 0)),
        "refresh_expires_at": time.time() + int(body.get("refresh_expires_in", 0)),
    })
    return JSONResponse({"ok": True, "message": "✅ TikTok token saved!"})

# ─── TikTok Token Info ────────────────────────────────────────────────────────

@app.get("/tiktok/token-info")
@app.get("/tiktok/token-info/")
async def tiktok_token_info():
    tokens = load_tokens()
    if not tokens:
        return JSONResponse({"ok": False, "error": "No token file"}, status_code=404)
    now = time.time()
    return JSONResponse({
        "ok":                 True,
        "has_access_token":   bool(tokens.get("access_token")),
        "has_refresh_token":  bool(tokens.get("refresh_token")),
        "access_expires_in":  round(float(tokens.get("expires_at", 0)) - now),
        "refresh_expires_in": round(float(tokens.get("refresh_expires_at", 0)) - now),
        "access_expired":     token_expired(tokens),
        "scope":              tokens.get("scope"),
        "open_id":            tokens.get("open_id"),
    })

# ─── TikTok Publish ───────────────────────────────────────────────────────────

@app.post("/tiktok/publish")
@app.post("/tiktok/publish/")
async def tiktok_publish(payload: dict):
    access_token, err = await get_valid_access_token()
    if err: return err

    file_id = payload.get("file_id") or payload.get("fileId")
    file_path = payload.get("file_path") or payload.get("filePath")

    if file_id:
        file_path = os.path.join(FILES_DIR, f"{file_id}.mp4")

    if not file_path or not os.path.exists(file_path):
        return JSONResponse({"ok": False, "error": "Missing file"}, status_code=400)

    video_size = os.path.getsize(file_path)

    init_body = {
        "post_info": {
            "title":           payload.get("title", "").strip() or "Posted via Ouin/Oual",
            "privacy_level":   payload.get("privacy_level", "PUBLIC_TO_EVERYONE"),
            "disable_comment": payload.get("disable_comment", False),
            "disable_duet":    payload.get("disable_duet", False),
            "disable_stitch":  payload.get("disable_stitch", False),
            "brand_content_toggle": payload.get("brand_content_toggle", False),
            "brand_organic_toggle": payload.get("brand_organic_toggle", False),
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
    data = init_json.get("data", {})
    if init_r.status_code != 200 or not data.get("upload_url"):
        return JSONResponse({"ok": False, "step": "init", "response": init_json}, status_code=init_r.status_code)

    publish_id = data["publish_id"]
    upload_url = data["upload_url"]

    with open(file_path, "rb") as f:
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
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
        )

    return JSONResponse({
        "ok": True,
        "publish_id": publish_id,
        "upload_http_status": put_r.status_code,
        "status": safe_json(status_r),
    })

# ─── Track Publish ────────────────────────────────────────────────────────────

@app.post("/track-publish")
@app.post("/track-publish/")
async def track_publish_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    try:
        result = await track_publish(body)
        return JSONResponse({
            "ok": True,
            "message": f"✅ Post tracked: {body.get('platform')} / {body.get('platform_post_id')}",
            "saved": result,
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

# ─── Sync Metrics ────────────────────────────────────────────────────────────

@app.post("/sync-metrics")
@app.post("/sync-metrics/")
async def sync_metrics_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    platform = (body.get("platform") or "").strip().lower()
    platform_post_id = (body.get("platform_post_id") or "").strip()

    if not platform or not platform_post_id:
        return JSONResponse(
            {"ok": False, "error": "platform and platform_post_id are required"},
            status_code=400
        )

    try:
        synced = await sync_metrics_for_post(platform, platform_post_id)
        if synced:
            return JSONResponse({
                "ok": True,
                "updated": 1,
                "metrics": synced.get("metrics", {}),
                "record": synced,
            })
        return JSONResponse({"ok": False, "error": "post_not_found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@app.get("/metrics/{platform}/{platform_post_id}")
async def get_metrics(platform: str, platform_post_id: str):
    post = get_post(platform, platform_post_id)
    if not post:
        return JSONResponse({"ok": False, "error": "post_not_found"}, status_code=404)
    return JSONResponse({"ok": True, "record": post})

@app.get("/metrics/all")
async def get_all_metrics():
    count = await run_sync_all()
    posts = get_all_posts()
    return JSONResponse({
        "ok": True,
        "updated_count": count,
        "count": len(posts),
        "records": posts,
    })

# ─── TikTok User Info ─────────────────────────────────────────────────────────

@app.get("/tiktok/userinfo")
@app.get("/tiktok/userinfo/")
async def tiktok_userinfo():
    access_token, err = await get_valid_access_token()
    if err: return err
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            "https://open.tiktokapis.com/v2/user/info/",
            params={"fields": "openid,display_name,avatar_url"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    body = safe_json(r)
    user = body.get("data", {}).get("user", {})
    return JSONResponse({
        "ok": True,
        "openid": user.get("openid"),
        "display_name": user.get("display_name"),
        "avatar_url": user.get("avatar_url"),
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
