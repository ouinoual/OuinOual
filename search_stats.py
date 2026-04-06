# search_stats.py
"""
طبقة بسيطة لتخزين وتحليل عمليات البحث:
- تخزين الكلمة الأصلية والمترجمة والبلد والتاريخ.
- إرجاع أكثر الكلمات بحثاً في كل بلد.
يُخزن كل شيء في ملف JSON واحد: search_stats.json
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Tuple

SEARCH_STATS_FILE = "search_stats.json"


def _load_stats() -> Dict:
    if not os.path.exists(SEARCH_STATS_FILE):
        return {}
    try:
        with open(SEARCH_STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_stats(data: Dict) -> None:
    try:
        with open(SEARCH_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("⚠️ Could not save search_stats.json:", e)


def record_search(
    country: str,
    raw_keyword: str,
    translated_keyword: str,
) -> None:
    """
    تُستدعى في كل عملية بحث:
    - country: رمز البلد (DZ, MA, FR...)
    - raw_keyword: النص الذي كتبه المستخدم (قد يكون بالعربية)
    - translated_keyword: الكلمة التي استُخدمت فعلياً في AliExpress (عادة بالإنجليزية)
    """
    country = (country or "XX").upper()
    raw_keyword = (raw_keyword or "").strip()
    translated_keyword = (translated_keyword or "").strip()

    if not raw_keyword:
        return

    stats = _load_stats()

    if country not in stats:
        stats[country] = {}

    country_data = stats[country]

    key = translated_keyword or raw_keyword

    if key not in country_data:
        country_data[key] = {
            "count": 0,
            "last_ts": None,
            "examples": [],
        }

    entry = country_data[key]
    entry["count"] = int(entry.get("count", 0)) + 1
    entry["last_ts"] = datetime.utcnow().isoformat()

    examples: List[str] = entry.get("examples") or []
    if raw_keyword not in examples:
        examples.append(raw_keyword)
        # نحدّ الأمثلة لعدم تضخّم الملف
        entry["examples"] = examples[-5:]

    stats[country] = country_data
    _save_stats(stats)


def get_top_searches(country: str, limit: int = 5) -> List[Tuple[str, int]]:
    """
    يرجع قائمة [(keyword, count), ...] لأكثر الكلمات بحثاً في هذا البلد.
    الكلمة هنا هي الكلمة المترجمة (المستخدمة مع AliExpress).
    """
    country = (country or "XX").upper()
    stats = _load_stats()
    country_data = stats.get(country) or {}

    items = [
        (kw, int(info.get("count", 0)))
        for kw, info in country_data.items()
    ]
    items.sort(key=lambda x: x[1], reverse=True)
    return items[:limit]
