# tracker.py ✅ v1.0 - Unified Tracking Layer
from __future__ import annotations
import json, os
from datetime import datetime
from typing import Dict, List, Optional, Any
import httpx

ANALYTICS_DB       = os.getenv("ANALYTICS_DB_PATH", "analytics_db.json")
TIKTOK_TOKENS_PATH = os.getenv("TOKENS_PATH", "tokens.json")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID= os.getenv("TELEGRAM_CHANNEL_ID", "")
FACEBOOK_PAGE_TOKEN= os.getenv("FACEBOOK_PAGE_TOKEN", "")
INSTAGRAM_TOKEN    = os.getenv("INSTAGRAM_USER_TOKEN", "")
INSTAGRAM_ACCT_ID  = os.getenv("INSTAGRAM_ACCOUNT_ID", "")

def _load_db() -> Dict:
    if not os.path.exists(ANALYTICS_DB):
        return {"posts": {}, "stats_history": []}
    try:
        with open(ANALYTICS_DB, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"posts": {}, "stats_history": []}

def _save_db(db: Dict) -> None:
    with open(ANALYTICS_DB, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def register_post(platform: str, post_id: str, product: Dict, extra: Optional[Dict] = None) -> str:
    db  = _load_db()
    key = f"{platform}:{post_id}"
    db["posts"][key] = {
        "platform":     platform,
        "post_id":      post_id,
        "product_id":   product.get("product_id") or product.get("productid"),
        "title":        product.get("main_product_name") or product.get("title", ""),
        "category":     product.get("category", ""),
        "price":        product.get("new_price"),
        "discount_pct": product.get("discount_pct"),
        "published_at": datetime.utcnow().isoformat(),
        "stats":        {},
        "last_fetched": None,
        **(extra or {}),
    }
    _save_db(db)
    return key

def list_posts(platform: Optional[str] = None) -> List[Dict]:
    db    = _load_db()
    posts = list(db["posts"].values())
    if platform:
        posts = [p for p in posts if p["platform"] == platform]
    return posts

def update_post_stats(platform: str, post_id: str, stats: Dict) -> None:
    db  = _load_db()
    key = f"{platform}:{post_id}"
    if key in db["posts"]:
        db["posts"][key]["stats"]        = stats
        db["posts"][key]["last_fetched"] = datetime.utcnow().isoformat()
        db["stats_history"].append({
            "key": key, "stats": stats,
            "fetched_at": datetime.utcnow().isoformat(),
        })
    _save_db(db)

def _load_tiktok_token() -> Optional[str]:
    if not os.path.exists(TIKTOK_TOKENS_PATH):
        return None
    with open(TIKTOK_TOKENS_PATH, "r") as f:
        tokens = json.load(f)
    return tokens.get("access_token")

async def fetch_tiktok_stats(video_id: str) -> Dict:
    access_token = _load_tiktok_token()
    if not access_token:
        return {"error": "no_token"}
    fields = "id,title,view_count,like_count,comment_count,share_count"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://open.tiktokapis.com/v2/video/query/",
            params={"fields": fields},
            json={"filters": {"video_ids": [video_id]}},
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        )
    if r.status_code != 200:
        return {"error": f"http_{r.status_code}"}
    videos = r.json().get("data", {}).get("videos", [])
    if not videos:
        return {"error": "no_data"}
    v        = videos[0]
    views    = v.get("view_count", 0) or 0
    likes    = v.get("like_count", 0) or 0
    comments = v.get("comment_count", 0) or 0
    shares   = v.get("share_count", 0) or 0
    return {
        "views": views, "likes": likes, "comments": comments, "shares": shares,
        "engagement_rate": round((likes + comments + shares) / max(views, 1) * 100, 2),
        "platform": "tiktok",
    }

async def fetch_telegram_stats(message_id: str, chat_id: Optional[str] = None) -> Dict:
    bot_token = TELEGRAM_BOT_TOKEN
    channel   = chat_id or TELEGRAM_CHANNEL_ID
    if not bot_token or not channel:
        return {"error": "missing_config"}
    base   = f"https://api.telegram.org/bot{bot_token}"
    result = {"platform": "telegram"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{base}/getMessageReactionCount", json={
            "chat_id": channel, "message_id": int(message_id),
        })
    if r.status_code == 200 and r.json().get("ok"):
        reactions_data    = r.json().get("result", {}).get("reactions", [])
        result["reactions"] = sum(item.get("count", 0) for item in reactions_data)
        result["reaction_breakdown"] = [
            {"emoji": item.get("type", {}).get("emoji", "?"), "count": item.get("count", 0)}
            for item in reactions_data
        ]
    else:
        result["reactions"] = 0
    return result

async def fetch_facebook_stats(post_id: str) -> Dict:
    if not FACEBOOK_PAGE_TOKEN:
        return {"error": "missing_FACEBOOK_PAGE_TOKEN"}
    params = {
        "metric": "post_impressions,post_engaged_users,post_reactions_by_type_total,post_clicks,post_shares",
        "access_token": FACEBOOK_PAGE_TOKEN,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"https://graph.facebook.com/v19.0/{post_id}/insights", params=params)
    if r.status_code != 200:
        return {"error": f"http_{r.status_code}", "detail": r.text[:200]}
    result: Dict[str, Any] = {"platform": "facebook"}
    for item in r.json().get("data", []):
        name = item.get("name")
        vals = item.get("values", [])
        val  = vals[-1].get("value", 0) if vals else 0
        if name == "post_impressions":
            result["impressions"]   = int(val) if isinstance(val,(int,float)) else 0
        elif name == "post_engaged_users":
            result["engaged_users"] = int(val) if isinstance(val,(int,float)) else 0
        elif name == "post_reactions_by_type_total":
            result["reactions"]     = sum(val.values()) if isinstance(val, dict) else 0
        elif name == "post_clicks":
            result["clicks"]        = int(val) if isinstance(val,(int,float)) else 0
        elif name == "post_shares":
            result["shares"]        = int(val) if isinstance(val,(int,float)) else 0
    return result

async def fetch_instagram_stats(media_id: str) -> Dict:
    if not INSTAGRAM_TOKEN:
        return {"error": "missing_INSTAGRAM_USER_TOKEN"}
    params = {
        "metric": "impressions,reach,likes,comments,shares,saved",
        "access_token": INSTAGRAM_TOKEN,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"https://graph.facebook.com/v19.0/{media_id}/insights", params=params)
    if r.status_code != 200:
        return {"error": f"http_{r.status_code}"}
    result: Dict[str, Any] = {"platform": "instagram"}
    for item in r.json().get("data", []):
        name = item.get("name")
        val  = item.get("values",[{}])[-1].get("value",0) if item.get("values") else item.get("value",0)
        result[name] = int(val) if isinstance(val,(int,float)) else 0
    return result

async def collect_all_stats(max_age_hours: int = 72) -> Dict[str, Dict]:
    db      = _load_db()
    results = {}
    now     = datetime.utcnow()
    FETCH   = {
        "tiktok":    lambda p: fetch_tiktok_stats(p["post_id"]),
        "telegram":  lambda p: fetch_telegram_stats(p["post_id"], p.get("chat_id")),
        "facebook":  lambda p: fetch_facebook_stats(p["post_id"]),
        "instagram": lambda p: fetch_instagram_stats(p["post_id"]),
    }
    for key, post in db["posts"].items():
        try:
            pub   = datetime.fromisoformat(post.get("published_at","2000-01-01"))
            age_h = (now - pub).total_seconds() / 3600
            if age_h > max_age_hours:
                continue
            fn = FETCH.get(post["platform"])
            if not fn:
                continue
            stats = await fn(post)
            if stats and "error" not in stats:
                update_post_stats(post["platform"], post["post_id"], stats)
                results[key] = stats
        except Exception as e:
            results[key] = {"error": str(e)}
    return results
