import os
import uuid
import secrets
import subprocess
import time
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


# Disable automatic 307 redirects between "/path" and "/path/" to avoid double-callback issues
app = FastAPI(redirect_slashes=False)

FILES_DIR = "files"
os.makedirs(FILES_DIR, exist_ok=True)
app.mount("/files", StaticFiles(directory=FILES_DIR), name="files")

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL")
TIKTOK_CLIENT_KEY = os.environ.get("TIKTOK_CLIENT_KEY")
TIKTOK_CLIENT_SECRET = os.environ.get("TIKTOK_CLIENT_SECRET")
TIKTOK_REDIRECT_URI = os.environ.get("TIKTOK_REDIRECT_URI")

# Add video.publish here
DEFAULT_SCOPE = "user.info.basic,video.upload,video.publish"

# Simple in-memory replay protection for authorization codes (best-effort)
USED_CODES = {}
USED_CODES_TTL_SECONDS = 10 * 60  # 10 minutes


def require_env(value: Optional[str], name: str):
    if not value:
        return None, JSONResponse(
            {"ok": False, "error": f"Missing environment variable: {name}"},
            status_code=500,
        )
    return value, None


def cleanup_used_codes():
    now = time.time()
    expired = [k for k, ts in USED_CODES.items() if (now - ts) > USED_CODES_TTL_SECONDS]
    for k in expired:
        USED_CODES.pop(k, None)


@app.get("/health")
@app.get("/health/")
def health():
    return {"ok": True}


@app.post("/extract")
@app.post("/extract/")
def extract(payload: dict):
    url = payload.get("url")
    if not url:
        return JSONResponse({"error": "Missing url"}, status_code=400)

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
        return JSONResponse({"error": "Missing PUBLIC_BASE_URL"}, status_code=500)

    return {"fileUrl": f"{PUBLIC_BASE_URL}/files/{file_id}.mp4"}


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
    # Store state in HttpOnly cookie to validate callback
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
    # Some services/proxies send HEAD; don't spend a code on that
    if request.method == "HEAD":
        return JSONResponse({"ok": True})

    if error:
        return JSONResponse(
            {"ok": False, "error": error, "error_description": error_description, "state": state},
            status_code=400,
        )

    if not code:
        return JSONResponse({"ok": False, "error": "Missing code", "state": state}, status_code=400)

    # Validate state against cookie
    cookie_state = request.cookies.get("tt_state")
    if not cookie_state or (state != cookie_state):
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

    # Clear state cookie after callback
    resp = JSONResponse({"ok": True, "state": state, "token_response": r.json()}, status_code=r.status_code)
    resp.delete_cookie("tt_state")
    return resp
