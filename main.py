# main.py ✅ v2.1 - Fixed track-publish
import os
import json
import time
import uuid
import secrets
from urllib.parse import urlencode
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

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
PUBLISHED_POSTS_FILE  = os.environ.get("PUBLISHED_POSTS_FILE", "publishedposts.json")

# ─── Helpers ────────────────────────────────────────────────────────────────

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

def load_published_posts():
    if not os.path.exists(PUBLISHED_POSTS_FILE):
        return []
    try:
        with open(PUBLISHED_POSTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []

def save_published_posts(items):
    with open(PUBLISHED_POSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

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

# ─── TikTok Publish Photo ─────────────────────────────────────────────────────

@app.post("/tiktok/publish_photo")
@app.post("/tiktok/publish_photo/")
async def tiktok_publish_photo(request: Request):
    access_token, err = await get_valid_access_token()
    if err: return err
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

    post_info    = body.get("post_info", {})
    source_info  = body.get("source_info", {})
    photo_images = source_info.get("photo_images", [])

    if not post_info:
        return JSONResponse({"ok": False, "error": "Missing post_info"}, status_code=400)
    if not photo_images:
        return JSONResponse({"ok": False, "error": "Missing photo_images"}, status_code=400)

    tiktok_payload = {
        "post_info": {
            "title":           post_info.get("title", ""),
            "description":     post_info.get("description", ""),
            "privacy_level":   post_info.get("privacy_level", "PUBLIC_TO_EVERYONE"),
            "disable_comment": post_info.get("disable_comment", False),
            "auto_add_music":  post_info.get("auto_add_music", True),
        },
        "source_info": {
            "source":            source_info.get("source", "PULL_FROM_URL"),
            "photo_cover_index": source_info.get("photo_cover_index", 0),
            "photo_images":      photo_images,
        },
        "post_mode":  body.get("post_mode", "DIRECT_POST"),
        "media_type": body.get("media_type", "PHOTO"),
    }

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/content/init/",
            json=tiktok_payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type":  "application/json; charset=UTF-8",
            },
        )

    resp   = safe_json(r)
    tk_err = resp.get("error", {})
    ok     = r.status_code == 200 and tk_err.get("code") == "ok"

    return JSONResponse({
        "ok":                   ok,
        "publish_id":           resp.get("data", {}).get("publish_id"),
        "tiktok_error_code":    tk_err.get("code"),
        "tiktok_error_message": tk_err.get("message"),
        "tiktok_response":      resp,
    })

# ─── TikTok Publish Video ─────────────────────────────────────────────────────

@app.post("/tiktok/publish_video")
@app.post("/tiktok/publish_video/")
async def tiktok_publish_video(request: Request):
    access_token, err = await get_valid_access_token()
    if err: return err
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

    video_url = body.get("video_url", "").strip()
    if not video_url:
        return JSONResponse({"ok": False, "error": "Missing video_url"}, status_code=400)

    tiktok_payload = {
        "post_info": {
            "title":           body.get("title", ""),
            "privacy_level":   body.get("privacy_level", "PUBLIC_TO_EVERYONE"),
            "disable_comment": body.get("disable_comment", False),
        },
        "source_info": {
            "source":    "PULL_FROM_URL",
            "video_url": video_url,
        },
    }

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/video/init/",
            json=tiktok_payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type":  "application/json; charset=UTF-8",
            },
        )

    resp   = safe_json(r)
    tk_err = resp.get("error", {})
    ok     = r.status_code == 200 and tk_err.get("code") == "ok"

    return JSONResponse({
        "ok":                   ok,
        "publish_id":           resp.get("data", {}).get("publish_id"),
        "tiktok_error_code":    tk_err.get("code"),
        "tiktok_error_message": tk_err.get("message"),
        "tiktok_response":      resp,
    })

# ─── TikTok Publish Status ────────────────────────────────────────────────────

@app.get("/tiktok/publish_status/{publish_id}")
async def tiktok_publish_status(publish_id: str):
    access_token, err = await get_valid_access_token()
    if err: return err
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
            json={"publish_id": publish_id},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type":  "application/json; charset=UTF-8",
            },
        )
    return JSONResponse({"ok": r.status_code == 200, "tiktok_response": safe_json(r)})

# ─── TikTok Resolve Video ID ──────────────────────────────────────────────────

@app.get("/tiktok/resolve_video_id/{publish_id}")
async def tiktok_resolve_video_id(publish_id: str):
    access_token, err = await get_valid_access_token()
    if err: return err

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
            json={"publish_id": publish_id},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type":  "application/json; charset=UTF-8",
            },
        )
    status_data = safe_json(r)
    status      = status_data.get("data", {}).get("status", "")
    video_id    = status_data.get("data", {}).get("publicaly_available_post_id", [None])[0]

    if not video_id:
        return JSONResponse({
            "ok":         False,
            "status":     status,
            "message":    "video_id not ready yet — retry in 30s",
            "publish_id": publish_id,
        })

    return JSONResponse({
        "ok":         True,
        "publish_id": publish_id,
        "video_id":   video_id,
        "status":     status,
    })

# ─── TikTok Video Stats ───────────────────────────────────────────────────────

@app.get("/tiktok/video_stats/{video_id}")
async def tiktok_video_stats(video_id: str):
    access_token, err = await get_valid_access_token()
    if err: return err

    fields = "id,title,view_count,like_count,comment_count,share_count"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://open.tiktokapis.com/v2/video/query/",
            params={"fields": fields},
            json={"filters": {"video_ids": [video_id]}},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type":  "application/json",
            },
        )

    data   = safe_json(r)
    videos = data.get("data", {}).get("videos", [])

    if not videos:
        return JSONResponse({"ok": False, "error": "no_data", "raw": data})

    v        = videos[0]
    views    = v.get("view_count", 0) or 0
    likes    = v.get("like_count", 0) or 0
    comments = v.get("comment_count", 0) or 0
    shares   = v.get("share_count", 0) or 0

    return JSONResponse({
        "ok":              True,
        "video_id":        video_id,
        "views":           views,
        "likes":           likes,
        "comments":        comments,
        "shares":          shares,
        "engagement_rate": round((likes + comments + shares) / max(views, 1) * 100, 2),
    })

# ─── Track Publish ────────────────────────────────────────────────────────────

@app.post("/track-publish")
@app.post("/track-publish/")
async def track_publish(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    platform         = (body.get("platform") or "").strip().lower()
    platform_post_id = (body.get("platform_post_id") or "").strip()

    if not platform or not platform_post_id:
        return JSONResponse(
            {"ok": False, "error": "platform and platform_post_id are required"},
            status_code=400
        )

    items = load_published_posts()
    item = {
        "id":               str(uuid.uuid4()),
        "product_id":       body.get("product_id", ""),
        "platform":         platform,
        "platform_post_id": platform_post_id,
        "publish_status":   body.get("publish_status", "published"),
        "published_at":     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "country":          body.get("country", ""),
        "category":         body.get("category", ""),
        "short_title":      body.get("short_title", ""),
        "source_mode":      body.get("source_mode", ""),
        "tracked_url":      body.get("tracked_url", ""),
        "destination_url":  body.get("destination_url", ""),
    }
    items.append(item)
    save_published_posts(items)

    return JSONResponse({
        "ok":      True,
        "key":     item["id"],
        "message": f"✅ Post tracked: {platform} / {platform_post_id}",
        "saved":   item,
    })
