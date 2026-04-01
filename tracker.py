from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

UNIFIED_DB_PATH = Path(os.getenv("UNIFIED_DB_PATH", "unified_db.json"))
TOKENS_PATH = Path(os.getenv("TOKENS_PATH", "tokens.json"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", os.getenv("CHANNEL_ID", "")).strip()
FACEBOOK_PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_TOKEN", "").strip()
INSTAGRAM_TOKEN = os.getenv("INSTAGRAM_USER_TOKEN", "").strip()

_db_lock = asyncio.Lock()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_db() -> Dict[str, Any]:
    return {
        "meta": {
            "version": 1,
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        },
        "posts": {},
        "stats_history": [],
        "events": [],
    }


def _normalize_db(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return _default_db()
    db = _default_db()
    db["meta"].update(data.get("meta", {}) if isinstance(data.get("meta"), dict) else {})
    db["posts"] = data.get("posts", {}) if isinstance(data.get("posts"), dict) else {}
    db["stats_history"] = data.get("stats_history", []) if isinstance(data.get("stats_history"), list) else []
    db["events"] = data.get("events", []) if isinstance(data.get("events"), list) else []
    db["meta"]["updated_at"] = utc_now_iso()
    return db


def _write_db_unlocked(db: Dict[str, Any]) -> None:
    db = _normalize_db(db)
    db["meta"]["updated_at"] = utc_now_iso()
    tmp_path = UNIFIED_DB_PATH.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    tmp_path.replace(UNIFIED_DB_PATH)


def _read_db_unlocked() -> Dict[str, Any]:
    if not UNIFIED_DB_PATH.exists():
        db = _default_db()
        _write_db_unlocked(db)
        return db
    try:
        with UNIFIED_DB_PATH.open("r", encoding="utf-8") as f:
            return _normalize_db(json.load(f))
    except Exception:
        return _default_db()


async def load_db() -> Dict[str, Any]:
    async with _db_lock:
        return deepcopy(_read_db_unlocked())


async def save_db(db: Dict[str, Any]) -> None:
    async with _db_lock:
        _write_db_unlocked(db)


async def register_post(payload: Dict[str, Any]) -> Dict[str, Any]:
    platform = (payload.get("platform") or "").strip().lower()
    platform_post_id = str(payload.get("platform_post_id") or "").strip()
    if not platform or not platform_post_id:
        raise ValueError("platform and platform_post_id are required")

    async with _db_lock:
        db = _read_db_unlocked()
        key = f"{platform}:{platform_post_id}"
        existing = db["posts"].get(key, {})
        post = {
            "id": existing.get("id") or str(uuid.uuid4()),
            "key": key,
            "platform": platform,
            "platform_post_id": platform_post_id,
            "product_id": payload.get("product_id", existing.get("product_id", "")),
            "publish_status": payload.get("publish_status", existing.get("publish_status", "published")),
            "published_at": existing.get("published_at") or payload.get("published_at") or utc_now_iso(),
            "country": payload.get("country", existing.get("country", "")),
            "category": payload.get("category", existing.get("category", "")),
            "short_title": payload.get("short_title", existing.get("short_title", "")),
            "source_mode": payload.get("source_mode", existing.get("source_mode", "")),
            "tracked_url": payload.get("tracked_url", existing.get("tracked_url", "")),
            "destination_url": payload.get("destination_url", existing.get("destination_url", "")),
            "channel_id": payload.get("channel_id", existing.get("channel_id", "")),
            "video_id": payload.get("video_id", existing.get("video_id", "")),
            "stats": existing.get("stats", {}),
            "metrics": existing.get("metrics", {}),
            "last_fetched": existing.get("last_fetched"),
            "raw": payload.get("raw", existing.get("raw", {})),
        }
        db["posts"][key] = post
        db["events"].append({
            "type": "post_registered",
            "key": key,
            "at": utc_now_iso()
        })
        _write_db_unlocked(db)
        return deepcopy(post)


def _load_tiktok_token() -> str:
    if not TOKENS_PATH.exists():
        return ""
    try:
        with TOKENS_PATH.open("r", encoding="utf-8") as f:
            return (json.load(f) or {}).get("access_token", "")
    except Exception:
        return ""


def _count_reactions(reactions_obj: Any) -> Optional[int]:
    if not isinstance(reactions_obj, dict):
        return None
    total = 0
    for item in reactions_obj.get("results", []) or []:
        if isinstance(item, dict):
            total += int(item.get("count", 0) or 0)
    return total if total > 0 else None


async def fetch_tiktok_stats(platform_post_id: str, explicit_video_id: str = "") -> Dict[str, Any]:
    token = _load_tiktok_token()
    if not token:
        return {"error": "no_token"}

    candidates = []
    if explicit_video_id:
        candidates.append(str(explicit_video_id).strip())
    if platform_post_id:
        raw = str(platform_post_id).strip()
        candidates.extend([raw, raw.replace("p_pub_url~v2.", "").split("!")[0]])
    candidates = [c for c in dict.fromkeys(candidates) if c]
    if not candidates:
        return {"error": "missing_video_id"}

    url = "https://open.tiktokapis.com/v2/video/query/"
    fields = ["id", "title", "view_count", "like_count", "comment_count", "share_count", "reach_user_count"]
    last_error = "no_data"

    async with httpx.AsyncClient(timeout=20) as client:
        for video_id in candidates:
            try:
                r = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json; charset=UTF-8",
                    },
                    json={"filters": {"video_ids": [video_id]}, "fields": fields, "max_count": 1},
                )
                data = r.json()
                videos = data.get("data", {}).get("videos") or []
                if not videos:
                    continue
                v = videos[0]
                views = int(v.get("view_count", 0) or 0)
                likes = int(v.get("like_count", 0) or 0)
                comments = int(v.get("comment_count", 0) or 0)
                shares = int(v.get("share_count", 0) or 0)
                reach = int(v.get("reach_user_count", 0) or 0)
                return {
                    "video_id": v.get("id") or video_id,
                    "views": views,
                    "likes": likes,
                    "comments": comments,
                    "shares": shares,
                    "reach": reach,
                    "engagement_score": likes + comments * 2 + shares * 3,
                    "engagement_rate": round(((likes + comments + shares) / max(views, 1)) * 100, 2),
                    "metrics_source": "tiktok_api",
                }
            except Exception as e:
                last_error = str(e)

    return {"error": last_error}


async def fetch_telegram_stats(message_id: str, chat_id: Optional[str] = None) -> Dict[str, Any]:
    channel = (chat_id or TELEGRAM_CHANNEL_ID or "").strip()
    if not TELEGRAM_BOT_TOKEN or not channel or not message_id:
        return {"error": "missing_config"}

    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    result: Dict[str, Any] = {"metrics_source": "telegram_bot"}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            member_resp = await client.get(f"{base}/getChatMemberCount", params={"chat_id": channel})
            member_json = member_resp.json()
            if member_json.get("ok"):
                result["channel_members"] = member_json.get("result")

            me_resp = await client.get(f"{base}/getMe")
            bot_id = me_resp.json().get("result", {}).get("id")
            if not bot_id:
                return {**result, "error": "bot_id_not_found"}

            fw_resp = await client.post(
                f"{base}/forwardMessage",
                json={
                    "chat_id": bot_id,
                    "from_chat_id": channel,
                    "message_id": int(message_id),
                    "disable_notification": True,
                },
            )
            fw_json = fw_resp.json()
            if not fw_json.get("ok"):
                return {**result, "error": fw_json.get("description", "forward_failed")}

            msg = fw_json.get("result", {})
            result["views"] = msg.get("views")
            result["forwards"] = msg.get("forwards")
            result["reactions"] = _count_reactions(msg.get("reactions", {}))
            result["engagement_score"] = int(result.get("reactions") or 0) + int(result.get("forwards") or 0) * 2

            fwd_id = msg.get("message_id")
            if fwd_id:
                await client.post(
                    f"{base}/deleteMessage",
                    json={"chat_id": bot_id, "message_id": fwd_id},
                )

            return result

    except Exception as e:
        return {**result, "error": str(e)}


async def fetch_facebook_stats(post_id: str) -> Dict[str, Any]:
    if not FACEBOOK_PAGE_TOKEN:
        return {"error": "missing_FACEBOOK_PAGE_TOKEN"}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                f"https://graph.facebook.com/v19.0/{post_id}/insights",
                params={
                    "metric": "post_impressions,post_engaged_users,post_reactions_by_type_total,post_clicks,post_shares",
                    "access_token": FACEBOOK_PAGE_TOKEN,
                },
            )

        if r.status_code != 200:
            return {"error": f"http_{r.status_code}", "detail": r.text[:200]}

        result: Dict[str, Any] = {"metrics_source": "facebook_graph"}
        for item in r.json().get("data", []):
            name = item.get("name")
            vals = item.get("values", [])
            val = vals[-1].get("value", 0) if vals else 0
            if name == "post_impressions":
                result["impressions"] = int(val) if isinstance(val, (int, float)) else 0
            elif name == "post_engaged_users":
                result["engaged_users"] = int(val) if isinstance(val, (int, float)) else 0
            elif name == "post_reactions_by_type_total":
                result["reactions"] = sum(val.values()) if isinstance(val, dict) else 0
            elif name == "post_clicks":
                result["clicks"] = int(val) if isinstance(val, (int, float)) else 0
            elif name == "post_shares":
                result["shares"] = int(val) if isinstance(val, (int, float)) else 0

        result["engagement_score"] = int(result.get("engaged_users") or 0) + int(result.get("shares") or 0) * 2
        return result

    except Exception as e:
        return {"error": str(e)}


async def fetch_instagram_stats(media_id: str) -> Dict[str, Any]:
    if not INSTAGRAM_TOKEN:
        return {"error": "missing_INSTAGRAM_USER_TOKEN"}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                f"https://graph.facebook.com/v19.0/{media_id}/insights",
                params={
                    "metric": "impressions,reach,likes,comments,shares,saved",
                    "access_token": INSTAGRAM_TOKEN,
                },
            )

        if r.status_code != 200:
            return {"error": f"http_{r.status_code}"}

        result: Dict[str, Any] = {"metrics_source": "instagram_graph"}
        for item in r.json().get("data", []):
            name = item.get("name")
            val = item.get("values", [{}])[-1].get("value", 0) if item.get("values") else item.get("value", 0)
            result[name] = int(val) if isinstance(val, (int, float)) else 0

        result["engagement_score"] = (
            int(result.get("likes") or 0)
            + int(result.get("comments") or 0) * 2
            + int(result.get("shares") or 0) * 3
            + int(result.get("saved") or 0) * 2
        )
        return result

    except Exception as e:
        return {"error": str(e)}


async def sync_post_metrics(post: Dict[str, Any]) -> Dict[str, Any]:
    platform = (post.get("platform") or "").strip().lower()
    post_id = str(post.get("platform_post_id") or "").strip()

    if platform == "tiktok":
        metrics = await fetch_tiktok_stats(post_id, explicit_video_id=str(post.get("video_id") or ""))
    elif platform == "telegram":
        metrics = await fetch_telegram_stats(post_id, post.get("channel_id"))
    elif platform == "facebook":
        metrics = await fetch_facebook_stats(post_id)
    elif platform == "instagram":
        metrics = await fetch_instagram_stats(post_id)
    else:
        metrics = {"error": "platform_not_supported"}

    metrics["last_metrics_at"] = utc_now_iso()
    return metrics


async def update_post_stats(key: str, stats: Dict[str, Any]) -> Dict[str, Any]:
    async with _db_lock:
        db = _read_db_unlocked()
        post = db["posts"].get(key)
        if not post:
            raise KeyError(key)

        post.setdefault("stats", {})
        post["stats"].update(stats)
        post["last_fetched"] = utc_now_iso()
        db["stats_history"].append({
            "key": key,
            "stats": deepcopy(stats),
            "fetched_at": utc_now_iso(),
        })
        _write_db_unlocked(db)
        return deepcopy(post)


async def run_sync_all(max_age_hours: int = 168) -> Dict[str, Any]:
    db = await load_db()
    now_ts = time.time()
    updated = 0
    errors: Dict[str, Any] = {}

    for key, post in db.get("posts", {}).items():
        published_at = str(post.get("published_at") or "")
        try:
            pub_ts = time.mktime(time.strptime(published_at, "%Y-%m-%dT%H:%M:%SZ"))
        except Exception:
            errors[key] = {"error": "bad_published_at"}
            continue

        if now_ts - pub_ts > max_age_hours * 3600:
            continue

        metrics = await sync_post_metrics(post)
        if metrics.get("error"):
            errors[key] = metrics
            continue

        await update_post_stats(key, metrics)
        updated += 1

    return {"updated": updated, "errors": errors}


async def list_posts(platform: Optional[str] = None) -> List[Dict[str, Any]]:
    db = await load_db()
    posts = list(db.get("posts", {}).values())
    if platform:
        p = platform.strip().lower()
        posts = [row for row in posts if (row.get("platform") or "").strip().lower() == p]
    return posts
