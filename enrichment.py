# enrichment.py
from __future__ import annotations

from typing import Dict, Any, List
import re

# =========================
# قواعد تنظيف العنوان
# =========================
_NOISE_PATTERNS = [
    r"\b\d+\s*-\s*\d+\s*(pcs|pc|pieces)\b",
    r"\b\d+\s*(pcs|pc|pieces)\b",
    r"\b(applicable\s*for|compatible\s*with)\b",
    r"\b(explosion[-\s]*proof|anti[-\s]*explosion)\b",
    r"\b(100%\s*new|brand\s*new|genuine|original)\b",
    r"\b(high\s*quality|top\s*quality|best\s*quality)\b",
    r"\bfree\s*shipping\b",
    r"\bfor\b\s*(iphone|samsung|xiaomi|huawei|ipad|oppo|vivo)\b",  # سنحاول إبقاء العلامة لاحقاً بشكل أنظف
]

_AR_REPLACE = {
    "tempered glass": "زجاج حماية",
    "screen protector": "واقي شاشة",
    "protective film": "فيلم حماية",
    "privacy": "خصوصية",
    "charger": "شاحن",
    "fast charge": "شحن سريع",
    "charging": "شحن",
    "usb c": "USB‑C",
    "type c": "USB‑C",
    "wireless": "لاسلكي",
    "bluetooth": "بلوتوث",
    "earbuds": "سماعات",
    "headphones": "سماعات رأس",
    "earphones": "سماعات",
    "case": "جراب",
    "cover": "غطاء",
}

# =========================
# Keywords عامة (مستوحاة من منطق accessory/device عندك)
# بدون استيراد affiliate_helpers لتفادي أي اعتماد جانبي
# =========================
DEVICE_HINTS = [
    "smartphone", "phone", "iphone", "android", "tablet", "ipad",
    "laptop", "notebook", "smart watch", "smartwatch",
    "earbuds", "headphones", "headset", "speaker", "soundbar",
    "vacuum", "air fryer", "coffee machine", "blender", "projector", "camera",
]

ACCESSORY_HINTS = [
    "case", "cover", "shell", "bumper",
    "screen protector", "tempered glass", "glass film", "protective film",
    "cable", "charging cable", "usb cable",
    "adapter", "converter", "charger", "wall charger", "car charger",
    "strap", "band", "wristband",
    "holder", "stand", "mount",
    "bag", "backpack", "pouch", "sleeve",
]

# =========================
# Rules دقيقة (اختيارية) + fallback عام
# =========================
RULES: List[Dict[str, Any]] = [
    {
        "category": "واقي شاشة",
        "keywords": ["screen protector", "tempered glass", "glass film", "protective film"],
        "why": "يحمي الشاشة من الخدوش والكسر.",
        "caution": "تأكد من موديل الجهاز قبل الطلب.",
        "tags": ["#حماية_شاشة", "#اكسسوارات_هاتف", "#ايفون"],
    },
    {
        "category": "شاحن",
        "keywords": ["charger", "adapter", "fast charge", "pd", "qc", "gan"],
        "why": "حل عملي للشحن اليومي.",
        "caution": "تحقق من نوع المنفذ والقدرة (W).",
        "tags": ["#شاحن", "#USB_C", "#تقنية"],
    },
    {
        "category": "كابل",
        "keywords": ["usb cable", "charging cable", "type-c cable", "usb c cable", "lightning cable"],
        "why": "بديل ممتاز ككابل احتياطي أو للاستخدام اليومي.",
        "caution": "تأكد من نوع المنفذ (USB‑C/Lightning) وطول الكابل.",
        "tags": ["#كابل", "#USB_C", "#اكسسوارات"],
    },
    {
        "category": "سماعات",
        "keywords": ["earbuds", "headset", "earphone", "earphones", "headphones", "jbl"],
        "why": "مناسبة للاستخدام اليومي والمكالمات.",
        "caution": "تأكد من التوافق مع جهازك وإصدار البلوتوث.",
        "tags": ["#سماعات", "#JBL", "#بلوتوث"],
    },
    {
        "category": "جراب/غطاء",
        "keywords": ["case", "cover", "shell", "bumper"],
        "why": "يحمي الجهاز ويقلل الخدوش والصدمات.",
        "caution": "تأكد من الموديل والمقاس قبل الطلب.",
        "tags": ["#جراب", "#اكسسوارات_هاتف", "#حماية"],
    },
]

def _extract_brand_model(title: str) -> List[str]:
    """
    يحاول استخراج علامة/موديل بسيطة لإضافتها كـ tags (اختياري).
    """
    t = title or ""
    out: List[str] = []

    # iPhone 16 / 15 / 14 + Pro/Max/Plus
    m = re.search(r"\biphone\s*\d{1,2}\b(?:\s*(pro|max|plus))?", t, flags=re.IGNORECASE)
    if m:
        out.append("#ايفون")

    # Samsung Galaxy Sxx / Axx
    if re.search(r"\bsamsung\b|\bgalaxy\b", t, flags=re.IGNORECASE):
        out.append("#سامسونج")

    # Xiaomi/Redmi
    if re.search(r"\bxiaomi\b|\bredmi\b", t, flags=re.IGNORECASE):
        out.append("#شاومي")

    # JBL
    if re.search(r"\bjbl\b", t, flags=re.IGNORECASE):
        out.append("#JBL")

    return out

def short_title(title: str, min_len: int = 60, max_len: int = 80) -> str:
    t = (title or "").strip()
    if not t:
        return "منتج مميز"

    t = re.sub(r"\s+", " ", t)

    for pat in _NOISE_PATTERNS:
        t = re.sub(pat, " ", t, flags=re.IGNORECASE)

    t = re.sub(r"[\[\]\(\)\{\}]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–|,;:")

    # استبدالات عربية خفيفة
    tl = t.lower()
    for k, v in _AR_REPLACE.items():
        if k in tl:
            t = re.sub(re.escape(k), v, t, flags=re.IGNORECASE)

    if len(t) <= max_len:
        return t

    cut = t[:max_len]
    if " " in cut and len(cut) > min_len:
        cut = cut.rsplit(" ", 1)[0]
    return cut.strip() + "…"

def classify_product(title: str) -> Dict[str, Any]:
    tl = (title or "").lower()

    # 1) Rules دقيقة
    for rule in RULES:
        if any(kw in tl for kw in rule.get("keywords", [])):
            tags = list(rule.get("tags", []))
            tags.extend(_extract_brand_model(title))
            tags = _dedupe_tags(tags)[:6]
            return {
                "category": rule.get("category", ""),
                "why": rule.get("why", ""),
                "caution": rule.get("caution", ""),
                "tags": tags,
                "kind": "rule",
            }

    # 2) Fallback عام: جهاز / اكسسوار
    is_accessory = any(k in tl for k in ACCESSORY_HINTS)
    is_device = any(k in tl for k in DEVICE_HINTS)

    if is_accessory and not is_device:
        tags = _dedupe_tags(["#اكسسوارات", "#تخفيضات"] + _extract_brand_model(title))[:6]
        return {
            "category": "اكسسوارات",
            "why": "إضافة مفيدة لتحسين الاستخدام اليومي.",
            "caution": "تأكد من التوافق (الموديل/المقاس/المنفذ) قبل الطلب.",
            "tags": tags,
            "kind": "fallback_accessory",
        }

    if is_device:
        tags = _dedupe_tags(["#تقنية", "#عروض"] + _extract_brand_model(title))[:6]
        return {
            "category": "جهاز",
            "why": "خيار مناسب لمن يريد ترقية أو شراء جهاز للاستخدام اليومي.",
            "caution": "راجع المواصفات جيداً (النسخة/المنفذ/الجهد/الملحقات).",
            "tags": tags,
            "kind": "fallback_device",
        }

    # 3) Unknown
    return {
        "category": "",
        "why": "",
        "caution": "",
        "tags": [],
        "kind": "unknown",
    }

def _dedupe_tags(tags: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for t in tags:
        if not isinstance(t, str):
            continue
        tt = t.strip()
        if not tt or not tt.startswith("#"):
            continue
        if tt in seen:
            continue
        seen.add(tt)
        out.append(tt)
    return out

def enrich_deal(deal: Dict[str, Any]) -> Dict[str, Any]:
    """
    لا يغير مفاتيح الحقول الأساسية (title/new_price/old_price/affiliate_link...).
    يضيف فقط deal['_enrich'].
    """
    try:
        raw_title = deal.get("title") or deal.get("product_title") or ""
        meta = classify_product(raw_title)
        st = short_title(raw_title)

        # نضيف category كبادئة فقط إذا كانت موجودة (اختياري + لطيف)
        if meta.get("category"):
            st = f'{meta["category"]}: {st}'

        deal["_enrich"] = {
            "short_title": st,
            "category": meta.get("category", ""),
            "why": meta.get("why", ""),
            "caution": meta.get("caution", ""),
            "tags": meta.get("tags", []),
            "kind": meta.get("kind", "unknown"),
        }
    except Exception:
        # لا نكسر النشر أبداً
        pass
    return deal
