import os
import json
import time
import hashlib
import hmac
from typing import Optional, Dict, Any

import requests
from dotenv import load_dotenv

# ------------------------------------------------------------------
# 1) Environment + Keys
# ------------------------------------------------------------------
load_dotenv()

APP_KEY = (os.getenv("ALI_APP_KEY") or "").strip()
APP_SECRET = (os.getenv("ALI_APP_SECRET") or "").strip()

DEFAULT_TRACKING_ID = (os.getenv("ALI_TRACKING_ID") or "").strip()

BASE_REST_URL = "https://api-sg.aliexpress.com/rest"
BASE_TOP_URL = BASE_REST_URL

TOKEN_FILE = os.getenv("ALI_TOKEN_FILE", "token.json").strip()

API_AUTH_CREATE = "/auth/token/create"
API_AUTH_REFRESH = "/auth/token/refresh"


# ------------------------------------------------------------------
# 2) Helpers: timestamp + signature
# ------------------------------------------------------------------
def build_timestamp_ms() -> str:
    return str(int(time.time() * 1000))


def build_signature(params: Dict[str, Any], api_name: str, app_secret: str) -> str:
    filtered = {k: str(v) for k, v in params.items() if v is not None and v != "" and k != "sign"}
    sorted_items = sorted(filtered.items(), key=lambda item: item[0])
    concatenated = "".join(f"{k}{v}" for k, v in sorted_items)
    string_to_sign = api_name + concatenated
    digest = hmac.new(
        app_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().upper()
    return digest


class AliExpress:
    """
    - يقرأ access_token/refresh_token من token.json
    - يجدد access_token تلقائياً عبر /auth/token/refresh عند الحاجة
    - يستدعي TOP APIs (affiliate.*) عبر /rest
    """

    def __init__(self, app_key: str = APP_KEY, app_secret: str = APP_SECRET):
        self.app_key = (app_key or "").strip()
        self.app_secret = (app_secret or "").strip()

        if not self.app_key or not self.app_secret:
            raise RuntimeError("Missing ALI_APP_KEY or ALI_APP_SECRET")

        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None

        # unix timestamps (seconds)
        self.expires_at: Optional[int] = None
        self.refresh_expires_at: Optional[int] = None
        self.last_refresh_at: Optional[int] = None

        self._load_token()

    # -------------------------------------------------------------
    # 2.1) token.json management
    # -------------------------------------------------------------
    def _load_token(self) -> None:
        if not os.path.exists(TOKEN_FILE):
            return

        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        self.access_token = data.get("access_token")
        self.refresh_token = data.get("refresh_token")

        expire_time_ms = data.get("expire_time")
        refresh_token_valid_time_ms = data.get("refresh_token_valid_time")

        created_at = data.get("created_at")
        expires_in = data.get("expires_in")
        refresh_expires_in = data.get("refresh_expires_in")

        if expire_time_ms:
            self.expires_at = int(int(expire_time_ms) / 1000)
        elif created_at and expires_in:
            self.expires_at = int(created_at + int(expires_in))

        if refresh_token_valid_time_ms:
            self.refresh_expires_at = int(int(refresh_token_valid_time_ms) / 1000)
        elif created_at and refresh_expires_in:
            self.refresh_expires_at = int(created_at + int(refresh_expires_in))

        self.last_refresh_at = data.get("last_refresh_at", created_at)

    def _save_token(self, token_data: Dict[str, Any]) -> None:
        now = int(time.time())
        token_data["created_at"] = token_data.get("created_at", now)
        token_data["last_refresh_at"] = now

        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(token_data, f, ensure_ascii=False, indent=2)

        # update memory state
        self.access_token = token_data.get("access_token")
        self.refresh_token = token_data.get("refresh_token") or self.refresh_token

        expire_time_ms = token_data.get("expire_time")
        refresh_token_valid_time_ms = token_data.get("refresh_token_valid_time")
        expires_in = token_data.get("expires_in")
        refresh_expires_in = token_data.get("refresh_expires_in")

        if expire_time_ms:
            self.expires_at = int(int(expire_time_ms) / 1000)
        elif expires_in:
            self.expires_at = now + int(expires_in)

        if refresh_token_valid_time_ms:
            self.refresh_expires_at = int(int(refresh_token_valid_time_ms) / 1000)
        elif refresh_expires_in:
            self.refresh_expires_at = now + int(refresh_expires_in)

        self.last_refresh_at = now

    def save_token(self, token_data: Dict[str, Any]) -> None:
        self._save_token(token_data)

    # -------------------------------------------------------------
    # 2.2) Auto-refresh logic
    # -------------------------------------------------------------
    def refresh_access_token(self) -> bool:
        """
        Calls /auth/token/refresh and updates token.json.
        """
        if not self.refresh_token:
            return False

        # If refresh token is expired, no point refreshing
        if self.refresh_expires_at and int(time.time()) >= int(self.refresh_expires_at):
            return False

        data = self.call_api_rest(API_AUTH_REFRESH, {"refresh_token": self.refresh_token})
        if isinstance(data, dict) and data.get("access_token"):
            self._save_token(data)
            return True

        return False

    def ensure_valid_token(self, skew_seconds: int = 300) -> None:
        """
        Refresh the access token if it will expire soon (default 5 minutes).
        """
        if not self.access_token or not self.expires_at:
            return

        now = int(time.time())
        if now >= (int(self.expires_at) - int(skew_seconds)):
            self.refresh_access_token()

    # -------------------------------------------------------------
    # 3) REST APIs (auth/token/create, auth/token/refresh)
    # -------------------------------------------------------------
    def call_api_rest(self, api_path: str, extra_params: Optional[Dict[str, Any]] = None) -> Optional[Dict]:
        url = BASE_REST_URL + api_path

        params: Dict[str, Any] = {
            "app_key": self.app_key,
            "timestamp": build_timestamp_ms(),
            "sign_method": "sha256",
        }

        if extra_params:
            params.update(extra_params)

        params["sign"] = build_signature(params, api_path, self.app_secret)

        resp = requests.get(url, params=params, timeout=30)
        try:
            return resp.json()
        except Exception:
            return None

    # -------------------------------------------------------------
    # 4) TOP APIs (affiliate.*)
    # -------------------------------------------------------------
    def call_api_top(self, method: str, extra_params: Optional[Dict[str, Any]] = None) -> Optional[Dict]:
        """
        Calls TOP endpoints (method=aliexpress.affiliate.*).
        Automatically refreshes token when needed, and retries once on IllegalAccessToken.
        """
        # refresh if expiring soon
        self.ensure_valid_token()

        url = BASE_TOP_URL

        params: Dict[str, Any] = {
            "method": method,
            "app_key": self.app_key,
            "timestamp": build_timestamp_ms(),
            "sign_method": "sha256",
            "format": "json",
            "v": "2.0",
        }

        if self.access_token:
            params["access_token"] = self.access_token

        if extra_params:
            params.update(extra_params)

        params["sign"] = build_signature(params, method, self.app_secret)

        resp = requests.get(url, params=params, timeout=30)

        try:
            data = resp.json()
        except Exception:
            return None

        # If token rejected, try refresh once then retry the same call once
        if isinstance(data, dict):
            code = data.get("code")
            if code == "IllegalAccessToken":
                if self.refresh_access_token():
                    return self.call_api_top(method, extra_params)

        return data


if __name__ == "__main__":
    ali = AliExpress()
    print("✅ AliExpress client initialized.")
