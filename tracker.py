import os
import json
import time
import uuid
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

UNIFIED_DB_PATH = Path(os.environ.get("UNIFIED_DB_PATH", "unified_db.json"))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TIKTOK_ACCESS_TOKEN_PATH = os.environ.get("TOKENSPATH", "tokens.json")
DEFAULT_CHANNEL_ID = (
    os.environ.get("CHANNEL_ID")
    or os.environ.get("TELEGRAM_CHANNEL_ID")
    or os.environ.get("CHANNEL_USERNAME")
    or ""
).strip()

DB_SCHEMA_VERSION = 1
_db_lock = asyncio.Lock()


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_parent_dir(path: Path) -> None:
    if path.parent and str(path.parent) not in ("", "."):
        path.parent.mkdir(parents=True, exist_ok=True)


def default_db() -> Dict[str, Any]:
    return {
        "meta": {
            "schema_version": DB_SCHEMA_VERSION,
            "created_at": utc_now(),
            "updated_at": utc_now(),
        },
        "posts": [],
        "clicks": [],
    }


def _normalize_posts_shape(rows: Any) -> List[Dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if isinstance(row, dict):
            row.setdefault("id", str(uuid.uuid4()))
            row.setdefault("metrics", {})
            row.setdefault("history", [])
            out.append(row)
    return out


def load_db() -> Dict[str, Any]:
    if not UNIFIED_DB_PATH.exists():
        db = default_db()
        save_db(db)
        return db

    try:
        data = json.loads(UNIFIED_DB_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = default_db()
        save_db(data)
        return data

    if isinstance(data, list):
        data = {
            "meta": {
                "schema_version": DB_SCHEMA_VERSION,
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "migrated_from": "legacy_list",
            },
            "posts": _normalize_posts_shape(data),
            "clicks": [],
        }
        save_db(data)
        return data

    if not isinstance(data, dict):
        data = default_db()
        save_db(data)
        return data

    data.setdefault("meta", {})
    data["meta"].setdefault("schema_version", DB_SCHEMA_VERSION)
    data["meta"].setdefault("created_at", utc_now())
    data["meta"]["updated_at"] = utc_now()
    data["posts"] = _normalize_posts_shape(data.get("posts", []))
    data.setdefault("clicks", [])
    return data


def save_db(db: Dict[str, Any]) -> None:
    ensure_parent_dir(UNIFIED_DB_PATH)
    db.setdefault("meta", {})
    db["meta"].setdefault("schema_version", DB_SCHEMA_VERSION)
    db["meta"].setdefault("created_at", utc_now())
    db["meta"]["updated_at"] = utc_now()
    UNIFIED_DB_PATH.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")


def load_posts() -> List[Dict[str, Any]]:
    return load_db().get("posts", [])


def save_posts(posts: List[Dict[str, Any]]) -> None:
    db = load_db()
    db["posts"] = _normalize_posts_shape(posts)
    save_db(db)


def load_tiktok_token() -> str:
    p = Path(TIKTOK_ACCESS_TOKEN_PATH)
    if not p.exists():
        return ""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return (data.get("access_token") or "").strip()
    except Exception:
        return ""


def count_reactions(reactions_obj: Optional[dict]) -> Optional[int]:
    if not reactions_obj:
        return None
    total = 0
    for r in reactions_obj.get("results", []) or []:
        total += int(r.get("count", 0) or 0)
    return total or None


async def fetch_tiktok_metrics(platform_post_id: str) -> Dict[str, Any]:
    token = load_tiktok_token()
    if not token or not platform_post_id:
        return {"metrics_source": "tiktok_api", "metrics_error": "missing_token_or_post_id"}

    video_id = platform_post_id.replace("v.pub_url/", "").split("?")[0].strip()
    if not video_id:
        return {"metrics_source": "tiktok_api", "metrics_error": "invalid_video_id"}

    url = "https://open.tiktokapis.com/v2/video/query/"
    body = {
        "filters": {"video_ids": [video_id]},
        "fields": ["id", "view_count", "like_count", "comment_count", "share_count", "reach_user_count"],
        "max_count": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
                json=body,
            )
            data = r.json()
            videos = ((data or {}).get("data") or {}).get("videos") or []
            if videos:
                v = videos[0]
                return {
                    "views": v.get("view_count"),
                    "likes": v.get("like_count"),
                    "comments": v.get("comment_count"),
                    "shares": v.get("share_count"),
                    "reach": v.get("reach_user_count"),
                    "video_id": v.get("id") or video_id,
                    "metrics_source": "tiktok_api",
                }
            return {
                "metrics_source": "tiktok_api",
                "metrics_error": "video_not_found",
                "api_response": data,
            }
    except Exception as e:
        return {"metrics_source": "tiktok_api", "metrics_error": str(e)}


async def fetch_telegram_metrics(channel_id: str, message_id: str) -> Dict[str, Any]:
    if not TELEGRAM_BOT_TOKEN or not message_id:
        return {"metrics_source": "telegram_bot", "metrics_error": "missing_token_or_message_id"}

    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    result: Dict[str, Any] = {"metrics_source": "telegram_bot"}

    try:
        async with httpx.AsyncClient(timeout=20) as c:
            if channel_id:
                r = await c.get(f"{base}/getChatMemberCount", params={"chat_id": channel_id})
                mc = r.json()
                if mc.get("ok"):
                    result["channel_members"] = mc.get("result")

            me_r = await c.get(f"{base}/getMe")
            me = me_r.json()
            bot_id = ((me or {}).get("result") or {}).get("id")

            if bot_id and channel_id:
                fw = await c.post(
                    f"{base}/forwardMessage",
                    json={
                        "chat_id": bot_id,
                        "from_chat_id": channel_id,
                        "message_id": int(message_id),
                        "disable_notification": True,
                    },
                )
                fw_data = fw.json()
                if fw_data.get("ok"):
                    msg = fw_data.get("result") or {}
                    result["views"] = msg.get("views")
                    result["forwards"] = msg.get("forwards")
                    result["reactions"] = count_reactions(msg.get("reactions"))
                    forwarded_id = msg.get("message_id")
                    if forwarded_id:
                        try:
                            await c.post(
                                f"{base}/deleteMessage",
                                json={"chat_id": bot_id, "message_id": forwarded_id},
                            )
                        except Exception:
                            pass
                else:
                    result["metrics_error"] = fw_data.get("description") or "forward_failed"

            return result
    except Exception as e:
        result["metrics_error"] = str(e)
        return result


def compute_engagement_score(metrics: Dict[str, Any]) -> float:
    views = float(metrics.get("views") or 0)
    likes = float(metrics.get("likes") or 0)
    comments = float(metrics.get("comments") or 0)
    shares = float(metrics.get("shares") or 0)
    forwards = float(metrics.get("forwards") or 0)
    reactions = float(metrics.get("reactions") or 0)

    if views <= 0:
        return 0.0

    score = ((likes * 1.0) + (comments * 2.0) + (shares * 3.0) + (forwards * 2.0) + (reactions * 1.5)) / views * 100
    return round(score, 4)


def merge_metrics(existing: Optional[Dict[str, Any]], new_metrics: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(existing or {})
    for key, value in (new_metrics or {}).items():
        if value is not None:
            merged[key] = value
    merged["last_metrics_at"] = utc_now()
    merged["engagement_score"] = compute_engagement_score(merged)
    return merged


def build_post_record(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "product_id": payload.get("product_id") or payload.get("productid"),
        "platform": (payload.get("platform") or "").strip().lower(),
        "platform_post_id": payload.get("platform_post_id") or payload.get("platformpostid"),
        "publish_status": payload.get("publish_status") or payload.get("publishstatus") or "published",
        "published_at": payload.get("published_at") or payload.get("publishedat") or utc_now(),
        "country": payload.get("country"),
        "category": payload.get("category"),
        "short_title": payload.get("short_title") or payload.get("shorttitle"),
        "source_mode": payload.get("source_mode") or payload.get("sourcemode"),
        "tracked_url": payload.get("tracked_url") or payload.get("trackedurl"),
        "destination_url": payload.get("destination_url") or payload.get("destinationurl"),
        "channel_id": payload.get("channel_id") or payload.get("channelid") or DEFAULT_CHANNEL_ID,
        "raw_publish_response": payload.get("raw_publish_response") or payload.get("rawpublishresponse") or {},
        "metrics": payload.get("metrics") or {},
        "history": [
            {
                "event": "publish_tracked",
                "at": utc_now(),
                "status": payload.get("publish_status") or payload.get("publishstatus") or "published",
            }
        ],
    }


def find_post(posts: List[Dict[str, Any]], platform: str, platform_post_id: str) -> Optional[Dict[str, Any]]:
    for row in posts:
        if row.get("platform") == platform and str(row.get("platform_post_id") or "") == str(platform_post_id or ""):
            return row
    return None


async def track_publish(payload: Dict[str, Any]) -> Dict[str, Any]:
    platform = (payload.get("platform") or "").strip().lower()
    product_id = payload.get("product_id") or payload.get("productid")
    platform_post_id = payload.get("platform_post_id") or payload.get("platformpostid")

    if not product_id:
        raise ValueError("Missing product_id")
    if not platform:
        raise ValueError("Missing platform")

    async with _db_lock:
        db = load_db()
        posts = db.get("posts", [])

        existing = None
        if platform_post_id:
            existing = find_post(posts, platform, platform_post_id)

        if existing:
            existing.update({
                "product_id": product_id,
                "publish_status": payload.get("publish_status") or payload.get("publishstatus") or existing.get("publish_status") or "published",
                "published_at": payload.get("published_at") or payload.get("publishedat") or existing.get("published_at") or utc_now(),
                "country": payload.get("country") or existing.get("country"),
                "category": payload.get("category") or existing.get("category"),
                "short_title": payload.get("short_title") or payload.get("shorttitle") or existing.get("short_title"),
                "source_mode": payload.get("source_mode") or payload.get("sourcemode") or existing.get("source_mode"),
                "tracked_url": payload.get("tracked_url") or payload.get("trackedurl") or existing.get("tracked_url"),
                "destination_url": payload.get("destination_url") or payload.get("destinationurl") or existing.get("destination_url"),
                "channel_id": payload.get("channel_id") or payload.get("channelid") or existing.get("channel_id") or DEFAULT_CHANNEL_ID,
                "raw_publish_response": payload.get("raw_publish_response") or payload.get("rawpublishresponse") or existing.get("raw_publish_response") or {},
            })
            existing.setdefault("history", []).append({
                "event": "publish_updated",
                "at": utc_now(),
                "status": existing.get("publish_status"),
            })
            save_db(db)
            return existing

        item = build_post_record(payload)
        posts.append(item)
        save_db(db)
        return item


async def sync_post_metrics(row: Dict[str, Any]) -> Dict[str, Any]:
    platform = (row.get("platform") or "").strip().lower()
    post_id = row.get("platform_post_id")
    now = utc_now()

    if platform == "tiktok":
        metrics = await fetch_tiktok_metrics(post_id)
    elif platform == "telegram":
        channel_id = row.get("channel_id") or DEFAULT_CHANNEL_ID
        metrics = await fetch_telegram_metrics(channel_id, post_id)
    else:
        metrics = {"metrics_source": "not_supported_yet", "metrics_error": f"unsupported_platform:{platform}"}

    metrics["last_metrics_at"] = now
    return metrics


async def sync_metrics_for_post(platform: str, platform_post_id: str) -> Optional[Dict[str, Any]]:
    async with _db_lock:
        db = load_db()
        posts = db.get("posts", [])
        row = find_post(posts, platform, platform_post_id)
        if not row:
            return None

    fresh = await sync_post_metrics(row)

    async with _db_lock:
        db = load_db()
        posts = db.get("posts", [])
        row = find_post(posts, platform, platform_post_id)
        if not row:
            return None
        row["metrics"] = merge_metrics(row.get("metrics"), fresh)
        row.setdefault("history", []).append({
            "event": "metrics_synced",
            "at": utc_now(),
            "platform": platform,
        })
        save_db(db)
        return row


async def run_sync_all(max_age_days: int = 7) -> int:
    async with _db_lock:
        db = load_db()
        snapshot = list(db.get("posts", []))

    now_ts = time.time()
    eligible = []
    for row in snapshot:
        try:
            pub_ts = time.mktime(time.strptime(row.get("published_at", ""), "%Y-%m-%dT%H:%M:%SZ"))
        except Exception:
            continue
        if now_ts - pub_ts > max_age_days * 24 * 3600:
            continue
        if (row.get("platform") or "") not in ("tiktok", "telegram"):
            continue
        if not row.get("platform_post_id"):
            continue
        eligible.append(row)

    updated = 0
    for row in eligible:
        synced = await sync_metrics_for_post(row.get("platform"), row.get("platform_post_id"))
        if synced:
            updated += 1
    return updated


def get_post(platform: str, platform_post_id: str) -> Optional[Dict[str, Any]]:
    posts = load_posts()
    return find_post(posts, platform, platform_post_id)


def get_all_posts() -> List[Dict[str, Any]]:
    return load_posts()
