import os
import json
import time
import secrets
from urllib.parse import urlencode
import asyncio

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from tracker import register_post, run_sync_all, list_posts, load_db

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
track_publish_lock = asyncio.Lock()


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

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data={
                "client_key": client_key,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
            },
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
        return None, JSONResponse({"ok": False, "error": "Not authorized. Visit /tiktok/login"}, status_code=400)

    if token_expired(tokens):
        tokens, err = await refresh_access_token()
        if err:
            return None, err

    return tokens["access_token"], None


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


@app.get("/tiktok/login")
@app.get("/tiktok/login/")
async def tiktok_login(request: Request):
    client_key, err = require_env(TIKTOK_CLIENT_KEY, "TIKTOK_CLIENT_KEY")
    if err:
        return err

    redirect_uri, err = require_env(TIKTOK_REDIRECT_URI, "TIKTOK_REDIRECT_URI")
    if err:
        return err

    params = {
        "client_key": client_key,
        "scope": request.query_params.get("scope", DEFAULT_SCOPE),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": secrets.token_urlsafe(16),
    }
    return RedirectResponse("https://www.tiktok.com/v2/auth/authorize/?" + urlencode(params))


@app.get("/tiktok/callback")
@app.get("/tiktok/callback/")
async def tiktok_callback(request: Request):
    cleanup_used_codes()

    code = request.query_params.get("code", "").strip()
    error = request.query_params.get("error", "").strip()

    if error:
        return JSONResponse({"ok": False, "error": error})
    if not code:
        return JSONResponse({"ok": False, "error": "Missing code"}, status_code=400)
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

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data={
                "client_key": client_key,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    body = safe_json(r)
    if r.status_code != 200 or not body.get("access_token"):
        return JSONResponse({"ok": False, "token_response": body}, status_code=r.status_code)

    save_tokens({
        **body,
        "expires_at": time.time() + int(body.get("expires_in", 0)),
        "refresh_expires_at": time.time() + int(body.get("refresh_expires_in", 0)),
    })
    return JSONResponse({"ok": True, "message": "✅ TikTok token saved!"})


@app.post("/track-publish")
@app.post("/track-publish/")
async def track_publish(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    async with track_publish_lock:
        try:
            saved = await register_post(body)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    return JSONResponse({
        "ok": True,
        "key": saved["key"],
        "message": f"✅ Post tracked: {saved['platform']} / {saved['platform_post_id']}",
        "saved": saved
    })


@app.post("/sync-metrics")
@app.post("/sync-metrics/")
async def sync_metrics():
    result = await run_sync_all()
    return JSONResponse({"ok": True, **result})


@app.get("/tracked-posts")
@app.get("/tracked-posts/")
async def tracked_posts(platform: str | None = None):
    posts = await list_posts(platform=platform)
    return JSONResponse({"ok": True, "count": len(posts), "items": posts})


@app.get("/db")
@app.get("/db/")
async def get_unified_db():
    db = await load_db()
    return JSONResponse({"ok": True, "db": db})
