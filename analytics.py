# analytics.py ✅ v1.0 - تحليل التفاعلات وتوجيه النشر
from __future__ import annotations
import json, os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from collections import defaultdict

ANALYTICS_DB = os.getenv("ANALYTICS_DB_PATH", "analytics_db.json")

def _load_db() -> Dict:
    if not os.path.exists(ANALYTICS_DB):
        return {"posts": {}, "stats_history": []}
    with open(ANALYTICS_DB, "r", encoding="utf-8") as f:
        return json.load(f)

PLATFORM_WEIGHTS = {
    "tiktok":    {"views": 1.0, "likes": 3.0, "comments": 5.0, "shares": 8.0},
    "telegram":  {"reactions": 5.0, "forwards": 8.0},
    "facebook":  {"impressions": 0.5, "reactions": 4.0, "clicks": 6.0, "shares": 8.0},
    "instagram": {"impressions": 0.5, "likes": 3.0, "comments": 5.0, "shares": 8.0, "saved": 6.0},
}

def compute_engagement_score(platform: str, stats: Dict) -> float:
    weights = PLATFORM_WEIGHTS.get(platform, {})
    return round(sum(float(stats.get(m,0) or 0) * w for m, w in weights.items()), 2)

def analyze_by_category(days: int = 30) -> List[Dict]:
    db, cutoff = _load_db(), datetime.utcnow() - timedelta(days=days)
    scores: Dict[str, List[float]] = defaultdict(list)
    for post in db["posts"].values():
        try:
            pub = datetime.fromisoformat(post.get("published_at","2000-01-01"))
        except Exception:
            continue
        if pub < cutoff:
            continue
        stats = post.get("stats", {})
        if not stats or "error" in stats:
            continue
        scores[post.get("category","غير محدد")].append(
            compute_engagement_score(post["platform"], stats)
        )
    result = [
        {"category": cat, "avg_score": round(sum(v)/len(v),2),
         "post_count": len(v), "total_score": round(sum(v),2)}
        for cat, v in scores.items()
    ]
    return sorted(result, key=lambda x: x["avg_score"], reverse=True)

def analyze_by_platform(days: int = 30) -> List[Dict]:
    db, cutoff = _load_db(), datetime.utcnow() - timedelta(days=days)
    scores: Dict[str, List[float]] = defaultdict(list)
    for post in db["posts"].values():
        try:
            pub = datetime.fromisoformat(post.get("published_at","2000-01-01"))
        except Exception:
            continue
        if pub < cutoff:
            continue
        stats = post.get("stats", {})
        if not stats or "error" in stats:
            continue
        scores[post["platform"]].append(compute_engagement_score(post["platform"], stats))
    result = [
        {"platform": p, "avg_score": round(sum(v)/len(v),2), "post_count": len(v)}
        for p, v in scores.items()
    ]
    return sorted(result, key=lambda x: x["avg_score"], reverse=True)

def analyze_best_posting_hours(platform: Optional[str] = None, days: int = 30) -> List[Dict]:
    db, cutoff = _load_db(), datetime.utcnow() - timedelta(days=days)
    hour_scores: Dict[int, List[float]] = defaultdict(list)
    for post in db["posts"].values():
        try:
            pub = datetime.fromisoformat(post.get("published_at","2000-01-01"))
        except Exception:
            continue
        if pub < cutoff or (platform and post.get("platform") != platform):
            continue
        stats = post.get("stats", {})
        if not stats or "error" in stats:
            continue
        hour_scores[pub.hour].append(compute_engagement_score(post["platform"], stats))
    result = [
        {"hour": h, "avg_score": round(sum(v)/len(v),2), "samples": len(v)}
        for h, v in hour_scores.items()
    ]
    return sorted(result, key=lambda x: x["avg_score"], reverse=True)

def get_top_posts(limit: int = 5, platform: Optional[str] = None) -> List[Dict]:
    db   = _load_db()
    data = []
    for key, post in db["posts"].items():
        stats = post.get("stats", {})
        if not stats or "error" in stats:
            continue
        if platform and post.get("platform") != platform:
            continue
        score = compute_engagement_score(post["platform"], stats)
        data.append({**post, "key": key, "score": score})
    return sorted(data, key=lambda x: x["score"], reverse=True)[:limit]

def _build_advice(best_cat, best_plat, timing) -> List[str]:
    advice = []
    if best_cat:
        advice.append(f"✅ انشر منتجات '{best_cat['category']}' — أعلى تفاعل (avg={best_cat['avg_score']}).")
    if best_plat:
        advice.append(f"✅ ركّز على {best_plat['platform']} — أفضل منصة (avg={best_plat['avg_score']}).")
    for plat, info in timing.items():
        advice.append(f"🕐 {plat}: أفضل وقت للنشر {info['best_hour_label']}.")
    return advice or ["📊 اجمع المزيد من البيانات لتوصيات أدق."]

def generate_publishing_recommendation(days: int = 30) -> Dict:
    cat_analysis  = analyze_by_category(days)
    plat_analysis = analyze_by_platform(days)
    best_cat      = cat_analysis[0]  if cat_analysis  else None
    best_plat     = plat_analysis[0] if plat_analysis else None
    timing        = {}
    for plat in ["tiktok","telegram","facebook","instagram"]:
        hours = analyze_best_posting_hours(platform=plat, days=days)
        if hours:
            timing[plat] = {"best_hour": hours[0]["hour"],
                            "best_hour_label": f"{hours[0]['hour']:02d}:00 UTC",
                            "avg_score": hours[0]["avg_score"]}
    if not best_cat:
        return {
            "status": "no_data",
            "message": "لا توجد بيانات بعد. انشر محتوى وانتظر جمع الإحصائيات.",
            "default_recommendation": {
                "categories_to_try": ["سماعات","ساعة ذكية","هاتف ذكي"],
                "best_times": {
                    "tiktok": "18:00-21:00", "telegram": "09:00-12:00",
                    "facebook": "12:00-15:00", "instagram": "19:00-22:00",
                },
            },
        }
    return {
        "status": "data_available",
        "generated_at":       datetime.utcnow().isoformat(),
        "summary":            {"best_category": best_cat["category"], "best_platform": best_plat["platform"] if best_plat else "?"},
        "top_categories":     cat_analysis[:5],
        "platform_perf":      plat_analysis,
        "best_hours_global":  analyze_best_posting_hours(days=days)[:5],
        "per_platform_timing":timing,
        "top_posts":          get_top_posts(5),
        "actionable_advice":  _build_advice(best_cat, best_plat, timing),
    }

def get_dashboard_summary() -> Dict:
    db    = _load_db()
    posts = list(db["posts"].values())
    by_pl = defaultdict(int)
    ok    = 0
    for p in posts:
        by_pl[p["platform"]] += 1
        if p.get("stats") and "error" not in p.get("stats",{}):
            ok += 1
    return {
        "total_posts_tracked": len(posts),
        "posts_with_stats":    ok,
        "by_platform":         dict(by_pl),
        "last_updated":        datetime.utcnow().isoformat(),
        "recommendation":      generate_publishing_recommendation(),
    }
