# utils.py ✅ v2.0 - مع أوصاف لكل منصة
from __future__ import annotations
from typing import Dict, Tuple, Optional, List, Any
import os, html, requests

def _format_price(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    try:
        return f"{float(value):.2f}"
    except Exception:
        return str(value)

def _format_money(value: Optional[str], currency: str) -> str:
    if not value:
        return f"غير متوفر {currency}"
    return f"{value} {currency}"

def _html_link(url: str, text: str) -> str:
    u = html.escape((url or "").strip(), quote=True)
    t = html.escape((text or "").strip())
    return f'<a href="{u}">👉 {t}</a>'

def _safe_text(s: Any) -> str:
    return html.escape(str(s or "").strip())

def _try_enrich(deal: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from enrichment import enrich_deal  # type: ignore
        enrich_deal(deal)
    except Exception:
        pass
    return deal

def _get_enrich(deal: Dict[str, Any]) -> Dict[str, Any]:
    e = deal.get("_enrich")
    return e if isinstance(e, dict) else {}

def _default_hook(category: str) -> str:
    hooks = {
        "واقي شاشة": "تحب تحمي شاشة جهازك بأقل تكلفة؟",
        "شاحن": "شحن أسرع بدون وجع رأس؟",
        "كابل": "كابل احتياطي ممتاز بسعر قوي؟",
        "سماعات": "تبحث عن سماعات عملية بسعر مناسب؟",
        "جراب/غطاء": "تبغى حماية لجهازك بدون ما تثقل عليه؟",
        "اكسسوارات": "إكسسوار بسيط يفرق معك يوميًا!",
        "جهاز": "تفكر ترقي جهازك؟ هذا عرض يستاهل.",
        "حقيبة": "حقيبة عملية لتنظيم أجهزتك يوميًا!",
    }
    return hooks.get((category or "").strip(), "صفقة اليوم قد تعجبك!")

def _build_tags_line(tags: List[str]) -> str:
    cleaned = [t.strip() for t in (tags or []) if isinstance(t, str) and t.strip().startswith("#")]
    return " ".join(cleaned[:10]) if cleaned else "#AliExpress #عروض #خصومات #DailyDeals #تخفيضات"

def _get_clean_title(deal: Dict[str, Any]) -> str:
    main = (deal.get("main_product_name") or deal.get("mainproductname") or "").strip()
    if main and len(main) > 5:
        return main
    raw = (deal.get("title") or deal.get("product_title") or "Product").strip()
    for sep in [" for ", " For ", " compatible ", " fits "]:
        idx = raw.find(sep)
        if idx > 5:
            return raw[:idx].strip()
    return raw[:80] if len(raw) > 80 else raw

def _detect_product_category(title: str) -> str:
    t = title.lower()
    if any(k in t for k in ["backpack","bag","pouch","satchel","sleeve","tote"]):
        return "حقيبة"
    if any(k in t for k in ["earbuds","earphones","headphones","headset","سماعة","سماعات"]):
        return "سماعات"
    if any(k in t for k in ["watch","smartwatch","ساعة"]):
        return "ساعة ذكية"
    if any(k in t for k in ["phone","smartphone","iphone","android","هاتف"]):
        return "هاتف ذكي"
    if any(k in t for k in ["tablet","ipad","لوح"]):
        return "تابلت"
    if any(k in t for k in ["laptop","notebook","macbook","لابتوب"]):
        return "لابتوب"
    if any(k in t for k in ["speaker","soundbar","مكبر"]):
        return "مكبر صوت"
    if any(k in t for k in ["charger","cable","شاحن","كابل"]):
        return "شاحن/كابل"
    if any(k in t for k in ["case","cover","جراب","غطاء"]):
        return "جراب/غطاء"
    return "جهاز"

def format_deal_message(deal: Dict[str, Any]) -> str:
    deal = _try_enrich(deal)
    e = _get_enrich(deal)
    clean_title = _get_clean_title(deal)
    title = _safe_text(e.get("short_title") or clean_title)
    new_price = deal.get("new_price") or deal.get("target_sale_price") or deal.get("sale_price")
    old_price = deal.get("old_price") or deal.get("target_original_price") or deal.get("original_price")
    currency = deal.get("currency") or "USD"
    new_p = _format_price(new_price)
    old_p = _format_price(old_price)
    discount = deal.get("discount_pct") or deal.get("discount")
    discount_str = f"-{discount:.2f}%" if isinstance(discount, (int, float)) else (discount or "")
    aff_link = str(deal.get("affiliate_link") or deal.get("affiliatelink") or deal.get("product_detail_url") or "").strip()
    category = str(e.get("category") or _detect_product_category(clean_title)).strip()
    why = str(e.get("why") or "مناسب لمن يبحث عن صفقة جيدة.").strip()
    caution = str(e.get("caution") or "السعر قد يختلف حسب الدولة والشحن.").strip()
    tags_line = _build_tags_line(e.get("tags", []))
    hook = _default_hook(category)
    lines = [f"⚡️ {hook}", "🔥 أفضل عرض اليوم على AliExpress"]
    if new_p and discount_str:
        lines.append(f"🔥 خصم {discount_str} • الآن بـ {_format_money(new_p, currency)}")
    elif new_p:
        lines.append(f"🔥 السعر الحالي: {_format_money(new_p, currency)}")
    lines.extend(["", f"📦 المنتج: {title}"])
    if old_p and new_p and old_p != new_p:
        try:
            if float(old_p) > float(new_p):
                lines.append(f"💰 السعر قبل: {_format_money(old_p, currency)}")
                lines.append(f"💸 السعر الآن: {_format_money(new_p, currency)}")
        except Exception:
            pass
    elif new_p:
        lines.append(f"💸 السعر: {_format_money(new_p, currency)}")
    if discount_str:
        lines.append(f"📉 التخفيض: {discount_str}")
    lines.extend([
        f"✅ {_safe_text(why)}", f"💡 ملاحظة: {_safe_text(caution)}", "",
        "🎯 افتح الرابط للتفاصيل.", "🔗 رابط الشراء:",
        _html_link(aff_link, "افتح العرض على AliExpress") if aff_link and aff_link != "#" else "⚠️ الرابط غير متوفر.",
        "", tags_line,
    ])
    return "\n".join(lines)

def format_best_deal_message(product: Dict[str, Any], country: str = "DZ") -> Tuple[str, Optional[str]]:
    product = _try_enrich(product)
    e = _get_enrich(product)
    clean_title = _get_clean_title(product)
    title = _safe_text(e.get("short_title") or clean_title)
    old_price = _format_price(product.get("old_price"))
    new_price = _format_price(product.get("new_price"))
    currency = product.get("currency") or "USD"
    discount_pct = product.get("discount_pct")
    discount_str = f"-{float(discount_pct):.2f}%" if discount_pct is not None else ""
    aff_link = (product.get("affiliate_link") or product.get("affiliatelink") or "").strip()
    image_url = (product.get("image_url") or product.get("imageurl") or product.get("product_main_image_url") or product.get("productMainImageUrl"))
    category = str(e.get("category") or _detect_product_category(clean_title)).strip()
    why = str(e.get("why") or "مناسب لمن يبحث عن صفقة جيدة.").strip()
    caution = str(e.get("caution") or "السعر قد يختلف حسب الدولة والشحن.").strip()
    tags_line = _build_tags_line(e.get("tags", []))
    lines = [f"⚡️ {_default_hook(category)}", "🔥 أفضل عرض اليوم على AliExpress"]
    if new_price and discount_str:
        lines.append(f"🔥 خصم {discount_str} • الآن بـ {_format_money(new_price, currency)}")
    elif new_price:
        lines.append(f"🔥 السعر الحالي: {_format_money(new_price, currency)}")
    lines.extend(["", f"📦 المنتج: {title}"])
    if old_price and new_price:
        try:
            if float(old_price) > float(new_price):
                lines.append(f"💰 السعر قبل: {_format_money(old_price, currency)}")
                lines.append(f"💸 السعر الآن: {_format_money(new_price, currency)}")
            else:
                lines.append(f"💸 السعر: {_format_money(new_price, currency)}")
        except Exception:
            lines.append(f"💸 السعر: {_format_money(new_price, currency)}")
    elif new_price:
        lines.append(f"💸 السعر: {_format_money(new_price, currency)}")
    if discount_str:
        lines.append(f"📉 التخفيض: {discount_str}")
    lines.extend([
        f"✅ {_safe_text(why)}", f"💡 ملاحظة: {_safe_text(caution)}", "",
        "🎯 افتح الرابط لتفاصيل أكثر.", "🔗 رابط الشراء:",
        _html_link(aff_link, "افتح العرض على AliExpress") if aff_link and aff_link != "#" else "⚠️ الرابط غير متوفر.",
        "", tags_line,
    ])
    return "\n".join(lines), image_url

def format_cheapest_deal_message(product: Dict[str, Any], country: str = "DZ") -> Tuple[str, Optional[str]]:
    product = _try_enrich(product)
    e = _get_enrich(product)
    clean_title = _get_clean_title(product)
    title = _safe_text(e.get("short_title") or clean_title)
    new_price = _format_price(product.get("new_price"))
    currency = product.get("currency") or "USD"
    aff_link = (product.get("affiliate_link") or product.get("affiliatelink") or "").strip()
    image_url = (product.get("image_url") or product.get("imageurl") or product.get("product_main_image_url") or product.get("productMainImageUrl"))
    category = str(e.get("category") or _detect_product_category(clean_title)).strip()
    caution = str(e.get("caution") or "تأكد من التوافق قبل الطلب.").strip()
    tags_line = _build_tags_line(e.get("tags", []))
    lines = [
        f"⚡️ {_default_hook(category)}", "🎯 عرض بديل أرخص", "",
        f"📦 المنتج: {title}",
    ]
    if new_price:
        lines.append(f"💸 أرخص سعر متاح: {_format_money(new_price, currency)}")
    lines.extend([
        f"💡 ملاحظة: {_safe_text(caution)}", "",
        "🔗 رابط الشراء الأرخص:",
        _html_link(aff_link, "افتح العرض البديل") if aff_link and aff_link != "#" else "⚠️ الرابط غير متوفر.",
        "", tags_line,
    ])
    return "\n".join(lines), image_url

def format_tiktok_caption(product: Dict[str, Any], country: str = "FR") -> str:
    clean_title = _get_clean_title(product)
    new_price = _format_price(product.get("new_price"))
    old_price = _format_price(product.get("old_price"))
    currency = product.get("currency") or "USD"
    discount_pct = product.get("discount_pct")
    discount_str = f"{float(discount_pct):.0f}%" if discount_pct else ""
    category_tags = {
        "سماعات": "#ecouteurs #bluetooth #audio",
        "ساعة ذكية": "#montre #smartwatch #sport",
        "هاتف ذكي": "#smartphone #telephone #android",
        "تابلت": "#tablette #ipad #android",
        "لابتوب": "#laptop #ordinateur #portable",
        "مكبر صوت": "#speaker #bluetooth #musique",
        "شاحن/كابل": "#chargeur #cable #usbc",
        "جراب/غطاء": "#coque #protection #accessoire",
        "حقيبة": "#sac #sacados #rangement",
        "جهاز": "#tech #gadget #hightech",
    }
    tags = category_tags.get(_detect_product_category(clean_title), "#tech #aliexpress #deal")
    lines = [f"💥 {clean_title}" + (f" — -{discount_str}" if discount_str else ""), ""]
    if new_price and old_price:
        try:
            if float(old_price) > float(new_price):
                lines.append(f"Avant: {old_price} {currency} → Maintenant: {new_price} {currency}")
        except Exception:
            pass
    elif new_price:
        lines.append(f"Prix: {new_price} {currency}")
    lines.extend(["", "👉 Lien dans la bio.", "", tags, "#aliexpress #bonplan #promo"])
    return "\n".join(lines)[:2200]

def format_instagram_caption(product: Dict[str, Any], country: str = "FR") -> str:
    clean_title = _get_clean_title(product)
    new_price = _format_price(product.get("new_price"))
    old_price = _format_price(product.get("old_price"))
    currency = product.get("currency") or "USD"
    discount_pct = product.get("discount_pct")
    discount_str = f"{float(discount_pct):.0f}%" if discount_pct else ""
    rating = product.get("rating")
    orders = product.get("orders")
    lines = [f"✨ {clean_title}", ""]
    if discount_str:
        lines.append(f"🏷️ Promo -{discount_str} sur AliExpress !")
    if new_price and old_price:
        try:
            if float(old_price) > float(new_price):
                lines.append(f"{old_price} {currency} → 💸 {new_price} {currency}")
        except Exception:
            pass
    elif new_price:
        lines.append(f"💸 {new_price} {currency}")
    if rating:
        try:
            lines.append(f"⭐ {float(rating):.1f}/5")
        except Exception:
            pass
    if orders and int(orders) > 100:
        lines.append(f"📦 +{orders} commandes")
    lines.extend([
        "", "🔗 Lien en bio !",
        "— — — — — — — — — —",
        "#aliexpress #bonplan #deal #promo #shopping #reduction #tech #gadget",
    ])
    return "\n".join(lines)[:2200]

def format_facebook_post(product: Dict[str, Any], country: str = "FR") -> str:
    clean_title = _get_clean_title(product)
    category = _detect_product_category(clean_title)
    new_price = _format_price(product.get("new_price"))
    old_price = _format_price(product.get("old_price"))
    currency = product.get("currency") or "USD"
    discount_pct = product.get("discount_pct")
    discount_str = f"-{float(discount_pct):.0f}%" if discount_pct else ""
    rating = product.get("rating")
    orders = product.get("orders")
    aff_link = (product.get("affiliate_link") or product.get("affiliatelink") or "").strip()
    descriptions = {
        "سماعات": "Idéal pour écouter de la musique et passer des appels clairement.",
        "ساعة ذكية": "Suit vos activités sportives et affiche les notifications.",
        "هاتف ذكي": "Smartphone performant avec excellent rapport qualité-prix.",
        "تابلت": "Tablette polyvalente pour travailler, lire ou regarder des vidéos.",
        "لابتوب": "Ordinateur portable léger et efficace pour bureau et déplacements.",
        "مكبر صوت": "Son puissant et clair, parfait pour la maison ou en déplacement.",
        "شاحن/كابل": "Chargez vos appareils rapidement et en toute sécurité.",
        "جراب/غطاء": "Protège votre appareil contre les chocs et les rayures.",
        "حقيبة": "Rangez et transportez facilement vos appareils et accessoires.",
        "جهاز": "Un excellent produit tech à prix réduit sur AliExpress.",
    }
    desc = descriptions.get(category, "Un bon produit en promotion sur AliExpress.")
    lines = [f"🛍️ BON PLAN — {clean_title}", "", desc, ""]
    if discount_str and old_price and new_price:
        try:
            if float(old_price) > float(new_price):
                lines.append(f"💰 Prix normal : {old_price} {currency}")
                lines.append(f"💸 Prix promo  : {new_price} {currency}  ({discount_str})")
        except Exception:
            pass
    elif new_price:
        lines.append(f"💸 Prix : {new_price} {currency}")
    if rating:
        try:
            lines.append(f"⭐ Évaluation : {float(rating):.1f}/5")
        except Exception:
            pass
    if orders and int(orders) > 50:
        lines.append(f"📦 +{orders} commandes")
    lines.append("")
    lines.append(f"🔗 {aff_link}" if aff_link and aff_link != "#" else "🔗 Lien en commentaire.")
    lines.extend(["", "#AliExpress #BonPlan #Deal #Shopping #Promo #Tech"])
    return "\n".join(lines)

def build_all_platform_captions(product: Dict[str, Any], country: str = "FR") -> Dict[str, str]:
    return {
        "telegram":    format_deal_message(product),
        "tiktok":      format_tiktok_caption(product, country),
        "instagram":   format_instagram_caption(product, country),
        "facebook":    format_facebook_post(product, country),
        "clean_title": _get_clean_title(product),
        "category":    _detect_product_category(_get_clean_title(product)),
    }

MAKE_WEBHOOK_URL = os.getenv("MAKE_WEBHOOK_URL", "")

def send_to_make_webhook(deal_data: Dict[str, Any]) -> bool:
    if not MAKE_WEBHOOK_URL or "YOUR_ID" in MAKE_WEBHOOK_URL:
        print("⚠️ MAKE_WEBHOOK_URL not configured.")
        return False
    try:
        resp = requests.post(MAKE_WEBHOOK_URL, json=deal_data, timeout=10)
        if 200 <= resp.status_code < 300:
            return True
        print("⚠️ Make webhook:", resp.status_code, resp.text)
        return False
    except Exception as e:
        print("❌ Webhook error:", e)
        return False
