# ============================================================
# metrics_tracker.py — أضف هذا الملف بجانب main.py
# ============================================================
import os, time, json, httpx
from pathlib import Path

PUBLISH_DB = Path(os.environ.get("PUBLISH_DB", "publish_log.json"))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TIKTOK_ACCESS_TOKEN_PATH = os.environ.get("TOKENS_PATH", "tokens.json")


# ─── Storage ────────────────────────────────────────────────

def load_db():
    if PUBLISH_DB.exists():
        return json.loads(PUBLISH_DB.read_text(encoding="utf-8"))
    return []


def save_db(data):
    PUBLISH_DB.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ─── TikTok ─────────────────────────────────────────────────

def load_tiktok_token():
    p = Path(TIKTOK_ACCESS_TOKEN_PATH)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8")).get("access_token", "")
    return ""


async def fetch_tiktok_metrics(platform_post_id: str) -> dict:
    """
    يستخدم TikTok v2 /video/query/ لجلب:
    view_count, like_count, comment_count, share_count
    """
    token = load_tiktok_token()
    if not token or not platform_post_id:
        return {}
    # publish_id ≠ video_id في بعض الحالات — نحاول كلا الاثنين
    video_id = platform_post_id.replace("p_pub_url~v2.", "").split("!")[0]
    url = "https://open.tiktokapis.com/v2/video/query/"
    fields = "id,view_count,like_count,comment_count,share_count,reach_user_count"
    body = {
        "filters": {"video_ids": [video_id]},
        "fields": fields.split(","),
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
                "views":       v.get("view_count"),
                "likes":       v.get("like_count"),
                "comments":    v.get("comment_count"),
                "shares":      v.get("share_count"),
                "reach":       v.get("reach_user_count"),
                "metrics_source": "tiktok_api",
            }
    except Exception as e:
        return {"metrics_error": str(e)}
    return {}


# ─── Telegram ───────────────────────────────────────────────

async def fetch_telegram_metrics(channel_id: str, message_id: str) -> dict:
    """
    يجلب:
    - عدد أعضاء القناة   → getChatMemberCount
    - مشاهدات الرسالة    → forwardMessage إلى Saved Messages ثم نقرأ views
    (الطريقة الوحيدة المتاحة في Bot API بدون MTProto)
    """
    if not TELEGRAM_BOT_TOKEN or not message_id:
        return {}
    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    result = {"metrics_source": "telegram_bot"}

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            # 1) عدد الأعضاء
            r = await c.get(f"{base}/getChatMemberCount",
                            params={"chat_id": channel_id})
            mc = r.json()
            if mc.get("ok"):
                result["channel_members"] = mc.get("result")

            # 2) مشاهدات الرسالة
            # نعيد توجيه الرسالة لـ Saved Messages (chat_id = bot itself)
            me_r = await c.get(f"{base}/getMe")
            bot_id = me_r.json().get("result", {}).get("id")
            if bot_id and message_id:
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
                    result["reactions"] = _count_reactions(
                        msg.get("reactions", {})
                    )
                    # نحذف رسالة التوجيه فوراً
                    fwd_id = msg.get("message_id")
                    if fwd_id:
                        await c.post(
                            f"{base}/deleteMessage",
                            json={"chat_id": bot_id, "message_id": fwd_id},
                        )
    except Exception as e:
        result["metrics_error"] = str(e)

    return result


def _count_reactions(reactions_obj: dict) -> int:
    if not reactions_obj:
        return 0
    total = 0
    for r in reactions_obj.get("results", []):
        total += r.get("count", 0)
    return total or None


# ─── Dispatcher ─────────────────────────────────────────────

async def sync_post_metrics(row: dict) -> dict:
    platform = row.get("platform", "")
    post_id  = row.get("platform_post_id", "")
    now_str  = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if platform == "tiktok":
        metrics = await fetch_tiktok_metrics(post_id)
    elif platform == "telegram":
        channel_id = row.get("channel_id", os.environ.get("CHANNEL_ID", ""))
        metrics = await fetch_telegram_metrics(channel_id, post_id)
    else:
        metrics = {"metrics_source": "not_supported_yet"}

    metrics["last_metrics_at"] = now_str
    return metrics


# ─── Main sync job ───────────────────────────────────────────

async def run_sync_all():
    """
    شغّل هذه الدالة بشكل دوري (مثلاً كل ساعة).
    تقرأ جميع المنشورات، تجلب المقاييس، تحدّث السجل.
    """
    rows = load_db()
    now = time.time()
    updated = 0
    for row in rows:
        pub_time_str = row.get("published_at", "")
        # تحديث فقط المنشورات خلال آخر 7 أيام
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