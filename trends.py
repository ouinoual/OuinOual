import time
import math
import logging
import re
from typing import List, Dict, Any, Optional
from urllib.parse import quote_plus

from pytrends.request import TrendReq


DEFAULT_TREND_SEEDS = [
    "smart", "wireless", "mini", "portable", "usb", "rechargeable",
    "home", "kitchen", "decor", "storage", "organizer", "cleaning",
    "beauty", "skincare", "makeup", "hair", "perfume", "wellness",
    "fashion", "bag", "shoes", "jewelry", "watch", "sunglasses",
    "phone", "case", "charger", "earbuds", "speaker", "gaming",
    "laptop", "tablet", "accessories", "gadget",
    "car", "motorcycle", "travel", "camping", "outdoor", "fitness",
    "baby", "kids", "toy", "pet", "office", "school",
    "gift", "seasonal", "summer", "winter", "ramadan", "christmas"
]

BANNED_SUBSTRINGS = {
    "blog",
    "news",
    "wiki",
    "wikipedia",
    "decathlon",
    "orsay",
    "thehometrotters",
    "youtube",
    "facebook",
    "instagram",
    "tiktok",
    "amazon",
    "ebay",
    "shein",
    "temu",
    "aliexpress plaza",
    "reddit",
    "pinterest",
    "recipe",
    "recette",
    "menu",
    "hotel",
    "flight",
    "train",
    "airbnb",
}

LOW_BUYING_INTENT_WORDS = {
    "ideas",
    "inspiration",
    "review",
    "reviews",
    "compare",
    "comparison",
    "how to",
    "tutorial",
    "outfit",
    "look",
    "blog",
    "article",
}

HIGH_BUYING_INTENT_WORDS = {
    "charger",
    "case",
    "cover",
    "watch",
    "earbuds",
    "speaker",
    "keyboard",
    "mouse",
    "projector",
    "bag",
    "organizer",
    "vacuum",
    "blender",
    "lamp",
    "tripod",
    "camera",
    "tablet",
    "phone",
    "android",
    "smart",
    "beauty",
    "device",
    "gadget",
}

PRODUCT_HINTS = {
    "watch",
    "earbuds",
    "speaker",
    "charger",
    "projector",
    "keyboard",
    "mouse",
    "bag",
    "phone",
    "tablet",
    "case",
    "vacuum",
    "lamp",
    "organizer",
    "tripod",
    "camera",
    "beauty",
    "gadget",
    "device",
}

MAX_QUERY_WORDS = 5
MIN_QUERY_CHARS = 3


def _build_aliexpress_url(query: str) -> str:
    return f"https://www.aliexpress.com/wholesale?SearchText={quote_plus(query)}"


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _safe_title(text: str) -> str:
    text = _normalize_spaces(text)
    if not text:
        return "Unknown"
    return text[:1].upper() + text[1:]


def _contains_banned_term(query: str) -> bool:
    q = query.lower()
    return any(term in q for term in BANNED_SUBSTRINGS)


def _looks_like_noise(query: str) -> bool:
    q = query.lower().strip()

    if not q:
        return True

    if len(q) < MIN_QUERY_CHARS:
        return True

    words = q.split()
    if len(words) > MAX_QUERY_WORDS:
        return True

    if re.search(r"(https?://|\.com|\.fr|\.net|\.org)", q):
        return True

    if re.fullmatch(r"[\W_]+", q):
        return True

    if _contains_banned_term(q):
        return True

    return False


def _buying_intent_score(query: str, source_seed: str) -> float:
    q = query.lower()
    seed = (source_seed or "").lower()
    score = 0.0

    for term in HIGH_BUYING_INTENT_WORDS:
        if term in q:
            score += 2.5

    for term in PRODUCT_HINTS:
        if term in q:
            score += 1.5

    for term in LOW_BUYING_INTENT_WORDS:
        if term in q:
            score -= 3.0

    if any(token in q for token in seed.split()):
        score += 1.0

    word_count = len(q.split())
    if 2 <= word_count <= 4:
        score += 1.5
    elif word_count == 1:
        score += 0.5
    else:
        score -= 1.5

    if re.search(r"\b(pro|max|mini|usb|bluetooth|wireless|smart|5g)\b", q):
        score += 1.0

    return score


def _trend_strength_score(value: Optional[int]) -> float:
    if value is None:
        return 0.0
    try:
        value = max(int(value), 0)
        return math.log10(value + 1) * 3.0
    except Exception:
        return 0.0


def _final_score(query: str, source_seed: str, value: Optional[int]) -> float:
    return round(_buying_intent_score(query, source_seed) + _trend_strength_score(value), 2)


def _is_good_trend(query: str, source_seed: str, value: Optional[int]) -> bool:
    if _looks_like_noise(query):
        return False

    score = _final_score(query, source_seed, value)
    return score >= 4.0


def get_shopping_trends_data(
    country_code: str = "FR",
    limit_per_seed: int = 3,
    max_results: int = 10,
    sleep_seconds: float = 0.8,
) -> List[Dict[str, Any]]:
    country_code = (country_code or "FR").upper()
    logging.info(f"Fetching Google Shopping trends for {country_code}")

    pytrends = TrendReq(
        hl="en-US",
        tz=60,
        timeout=(10, 25),
        retries=3,
        backoff_factor=0.5,
    )

    found = set()
    candidates: List[Dict[str, Any]] = []

    try:
        for kw in DEFAULT_TREND_SEEDS:
            try:
                pytrends.build_payload([kw], cat=18, geo=country_code, timeframe="now 7-d")
                related_queries = pytrends.related_queries() or {}
                kw_data = related_queries.get(kw) or {}
                rising = kw_data.get("rising")

                if rising is None or rising.empty:
                    time.sleep(sleep_seconds)
                    continue

                top_rising = rising.head(limit_per_seed)

                for _, row in top_rising.iterrows():
                    trend_word = _normalize_spaces(str(row.get("query", "")))
                    if not trend_word:
                        continue

                    norm = trend_word.lower()
                    if norm in found:
                        continue
                    found.add(norm)

                    raw_value = row.get("value")
                    try:
                        value = int(raw_value)
                    except Exception:
                        value = None

                    score = _final_score(trend_word, kw, value)
                    good = _is_good_trend(trend_word, kw, value)

                    item = {
                        "query": trend_word,
                        "value": value,
                        "country": country_code,
                        "source_seed": kw,
                        "aliexpress_url": _build_aliexpress_url(trend_word),
                        "score": score,
                        "is_good": good,
                    }

                    if good:
                        candidates.append(item)

                time.sleep(sleep_seconds)

            except Exception as inner_error:
                logging.warning(f"Trend seed failed for '{kw}' in {country_code}: {inner_error}")
                time.sleep(sleep_seconds)
                continue

        candidates.sort(
            key=lambda x: (
                x.get("score", 0),
                x.get("value") is not None,
                x.get("value") or -1,
            ),
            reverse=True,
        )

        return candidates[:max_results]

    except Exception as e:
        logging.error(f"Failed to fetch shopping trends for {country_code}: {e}")
        return []


def format_shopping_trends_message(
    trends: List[Dict[str, Any]],
    country_code: str = "FR",
    max_items: int = 8,
    include_footer: bool = True,
) -> str:
    country_code = (country_code or "FR").upper()

    if not trends:
        return f"⚠️ لم يتم العثور على ترندات تجارية قوية اليوم في {country_code}."

    lines = [
        f"🛍 ترندات تسوق قابلة للنشر في ({country_code}) - آخر 7 أيام",
        "",
    ]

    for item in trends[:max_items]:
        query = item.get("query", "Unknown")
        value = item.get("value")
        seed = item.get("source_seed", "")
        ali_url = item.get("aliexpress_url", "")
        score = item.get("score")

        lines.append(f"🔥 {_safe_title(query)}")
        lines.append(f"📈 صعود: {value}%" if value is not None else "📈 صعود: غير متوفر")
        lines.append(f"⭐ Score: {score}" if score is not None else "⭐ Score: N/A")
        if seed:
            lines.append(f"🧭 المصدر: {seed}")
        if ali_url:
            lines.append(f'🛒 <a href="{ali_url}">بحث في AliExpress</a>')
        lines.append("────────────")

    if include_footer:
        lines.append("")
        lines.append("💡 هذه القائمة مفلترة تجاريًا وجاهزة للإرسال إلى Make لاختيار الأفضل للنشر.")

    return "\n".join(lines)


def get_shopping_trends(
    country_code: str = "FR",
    limit_per_seed: int = 3,
    max_results: int = 10,
) -> str:
    trends = get_shopping_trends_data(
        country_code=country_code,
        limit_per_seed=limit_per_seed,
        max_results=max_results,
    )
    return format_shopping_trends_message(
        trends=trends,
        country_code=country_code,
        max_items=max_results,
    )
