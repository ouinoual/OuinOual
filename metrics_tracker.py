import os
import time
import json
from pathlib import Path

import httpx

PUBLISH_DB = Path(os.environ.get("PUBLISH_DB", "publish_log.json"))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TIKTOK_ACCESS_TOKEN_PATH = os.environ.get("TOKENS_PATH", "tokens.json")


# ─────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────
def load_db():
    if PUBLISH_DB.exists():
        try:
            data = json.loads(PUBLISH_DB.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def save_db(data):
    PUBLISH_DB.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# ─────────────────────────────────────────────
# TikTok
# ─────────────────────────────────────────────
def load_tiktok_token():
    p = Path(TIKTOK_ACCESS_TOKEN_PATH)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("access_token", "")
        except Exception:
            return ""
    return ""


async def fetch_tiktok_metrics(platform_post_id: str) -> dict:
    """
    يحاول جلب:
    view_count, like_count, comment_count, share_count, reach_user_count
    """
    token = load_tiktok_token()
    if not token or not platform_post_id:
        return {}

    video_id = str(platform_post_id).replace("p_pub_url~v2.", "").split("!")[0]
    url = "https://open.tiktokapis.com/v2/video/query/"
    body = {
        "filters": {"video_ids": [video_id]},
        "fields": [
            "id",
            "view_count",
            "like_count",
            "comment_count",
            "share_count",
            "reach_user_count",
        ],
        "max_count": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
                json=body,
            )

        data = r.json()
        videos = data.get("data", {}).get("videos") or []
        if videos:
            v = videos[0]
            return {
                "views": v.get("view_count"),
                "likes": v.get("like_count"),
                "comments": v.get("comment_count"),
                "shares": v.get("share_count"),
                "reach": v.get("reach_user_count"),
                "metrics_source": "tiktok_api",
            }
    except Exception as e:
        return {"metrics_error": str(e)}

    return {}


# ─────────────────────────────────────────────
# Telegram
# ─────────────────────────────────────────────
def _count_reactions(reactions_obj: dict) -> int:
    if not reactions_obj:
        return 0
    total = 0
    for r in reactions_obj.get("results", []):
        total += r.get("count", 0)
    return total or None


async def fetch_telegram_metrics(channel_id: str, message_id: str) -> dict:
    """
    يجلب:
    - عدد أعضاء القناة عبر getChatMemberCount
    - مشاهدات الرسالة عبر forwardMessage ثم قراءة views
    """
    if not TELEGRAM_BOT_TOKEN or not message_id:
        return {}

    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    result = {"metrics_source": "telegram_bot"}

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            if channel_id:
                r = await c.get(
                    f"{base}/getChatMemberCount",
                    params={"chat_id": channel_id}
                )
                mc = r.json()
                if mc.get("ok"):
                    result["channel_members"] = mc.get("result")

            me_r = await c.get(f"{base}/getMe")
            bot_id = me_r.json().get("result", {}).get("id")

            if bot_id and message_id and channel_id:
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
                    msg = fw_data["result"]
                    result["views"] = msg.get("views")
                    result["forwards"] = msg.get("forwards")
                    result["reactions"] = _count_reactions(msg.get("reactions", {}))

                    fwd_id = msg.get("message_id")
                    if fwd_id:
                        await c.post(
                            f"{base}/deleteMessage",
                            json={"chat_id": bot_id, "message_id": fwd_id},
                        )
    except Exception as e:
        result["metrics_error"] = str(e)

    return result


# ─────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────
async def sync_post_metrics(row: dict) -> dict:
    platform = row.get("platform", "")
    post_id = row.get("platform_post_id", "")
    now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if platform == "tiktok":
        metrics = await fetch_tiktok_metrics(post_id)
    elif platform == "telegram":
        channel_id = row.get("channel_id", os.environ.get("CHANNEL_ID", ""))
        metrics = await fetch_telegram_metrics(channel_id, post_id)
    else:
        metrics = {"metrics_source": "not_supported_yet"}

    metrics["last_metrics_at"] = now_str
    return metrics


# ─────────────────────────────────────────────
# Main sync job
# ─────────────────────────────────────────────
async def run_sync_all():
    """
    تحدث فقط منشورات آخر 7 أيام
    والمنصات المدعومة حاليًا: TikTok و Telegram
    """
    rows = load_db()
    now = time.time()
    updated = 0

    for row in rows:
        pub_time_str = row.get("published_at", "")
        try:
            pub_ts = time.mktime(time.strptime(pub_time_str, "%Y-%m-%dT%H:%M:%SZ"))
        except Exception:
            continue

        if now - pub_ts > 7 * 24 * 3600:
            continue

        if row.get("platform") not in ("tiktok", "telegram"):
            continue

        metrics = await sync_post_metrics(row)
        if not row.get("metrics"):
            row["metrics"] = {}
        row["metrics"].update(metrics)
        updated += 1

    save_db(rows)
    return updated
