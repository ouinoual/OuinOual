# affiliate_helpers.py ✅ v3.0 - مصحح بالكامل
import json
import os
import re
from typing import List, Dict, Optional, Tuple, Set
from datetime import datetime, timedelta
from ali_api import AliExpress

ali = AliExpress()

DEFAULT_TRACKING_ID = (
    os.getenv("ALI_TRACKING_ID") or os.getenv("ALITRACKINGID") or ""
).strip()

LAST_DEALS_FILE = "last_deals.json"
LAST_DEALS_MAX_AGE_DAYS = 1
DEFAULT_COUNTRY = "DZ"

def is_api_ok(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    code = data.get("code")
    if code is None:
        return True
    try:
        return int(str(code)) in (0, 200)
    except Exception:
        return False

def parse_api_result(data: dict, method: str) -> Optional[dict]:
    if not isinstance(data, dict):
        return None
    method_key = method.replace(".", "_") + "_response"
    if method_key in data and isinstance(data[method_key], dict):
        inner = data[method_key]
        resp_result = inner.get("resp_result")
        if isinstance(resp_result, dict):
            return resp_result.get("result")
    resp_result = data.get("resp_result")
    if isinstance(resp_result, dict):
        return resp_result.get("result")
    result = data.get("result")
    if isinstance(result, dict):
        return result
    return None

GENERIC_QUERY_STOPWORDS = {
    "the","and","for","with","from","this","that","these","those",
    "en","de","la","le","du","des","pour","avec","sur","et",
    "un","une","aux","les","au","par","dans",
    "a","an","of","to","by","or","is","are",
}

def normalize_search_text(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def tokenize_search_text(text: str) -> List[str]:
    tokens = normalize_search_text(text).split()
    return [t for t in tokens if len(t) >= 2 and t not in GENERIC_QUERY_STOPWORDS]

def get_title_tokens(text: str) -> Set[str]:
    return set(tokenize_search_text(text))

def contains_any_phrase(text: str, phrases: Set[str]) -> bool:
    return any(p in text for p in phrases if p)

def extract_main_product_name(title: str) -> str:
    if not title:
        return title
    for sep in [" for ", " compatible ", " fits ", " suitable "]:
        idx = title.lower().find(sep)
        if idx > 5:
            return title[:idx].strip()
    return title

DEVICE_KEYWORDS = [
    "smartphone","android phone","iphone","tablet","ipad",
    "laptop","notebook","macbook","desktop pc","mini pc",
    "smart watch","smartwatch","earbuds","earphones","headphones","headset",
    "bluetooth speaker","wireless speaker","desktop speaker","soundbar",
    "projector","camera","monitor","gaming monitor","television","tv",
    "router","printer","vacuum cleaner","robot vacuum","air fryer",
    "coffee machine","blender","electric kettle","rice cooker",
    "synthesizer","modular synthesizer","eurorack synthesizer",
    "midi keyboard","sampler","sequencer","audio mixer",
]

STRONG_DEVICE_PHRASES = {
    "bluetooth speaker","wireless speaker","desktop speaker",
    "gaming headset","smart watch","robot vacuum","vacuum cleaner",
    "coffee machine","air fryer","mini pc","desktop pc",
    "gaming monitor","modular synthesizer","eurorack synthesizer",
    "midi keyboard","audio mixer",
}

DEVICE_TOKENS = {
    "smartphone","phone","iphone","android",
    "tablet","ipad","laptop","notebook","macbook","desktop","computer","pc","minipc",
    "smartwatch","watch","wearable","tracker",
    "earbuds","earphones","headphones","headset","speaker","soundbar",
    "subwoofer","amplifier","receiver",
    "projector","camera","webcam","dashcam",
    "monitor","display","television","tv",
    "router","modem","printer","scanner",
    "vacuum","cleaner","fryer","coffee","espresso","blender","mixer",
    "juicer","processor","kettle","cooker","toaster","grill","microwave",
    "washer","dryer","oven","humidifier","purifier","heater","fan",
    "console","controller","drone","robot",
    "synthesizer","synth","eurorack","modular","midi","keyboard",
    "sampler","sequencer","preamp","equalizer","compressor",
    "multimeter","oscilloscope","microscope","telescope","binoculars",
}

ACCESSORY_TOKENS = {
    "case","cover","bumper","shell","housing","bezel",
    "protector","film","glass",
    "strap","band","holder","stand","mount",
    "sleeve","pouch","bag","backpack","satchel","tote",
    "charger","cable","adapter","dock","base","hub",
    "replacement","repair","spare","digitizer",
    "frame","sticker","decal","keychain","lanyard",
}

ACCESSORY_PHRASES = {
    "phone case","iphone case","tablet case","ipad case",
    "laptop case","macbook case","watch case","watch band","watch strap",
    "screen protector","tempered glass","glass film","protective film",
    "keyboard cover","mouse pad","usb cable","charging cable",
    "type c cable","type c charger","usb c charger",
    "wall charger","car charger","wireless charger",
    "charging dock","charging base","power adapter",
    "phone holder","phone stand","phone mount",
    "tablet stand","tablet holder","laptop sleeve","carrying case",
    "replacement battery","screen replacement","lcd replacement",
    "digitizer replacement","back cover","spare part","repair part","repair kit",
    "tablet bag","laptop bag","tablet backpack","laptop backpack",
    "school bag","travel bag","shoulder bag",
}

ACCESSORY_CONTEXT_HINTS = {
    "compatible","protective","replacement","strap","band",
    "holder","mount","stand","cover","case","charger","cable",
    "bag","backpack","pouch",
}

DEVICE_CONTEXT_HINTS = {
    "bluetooth","wireless","gaming","smart","digital","electric",
    "stereo","hifi","portable","desktop","modular","eurorack","midi",
}

DEVICE_INTENT_TERMS = {
    "smartphone","phone","iphone","android","tablet","ipad",
    "laptop","notebook","macbook","smartwatch","watch",
    "earbuds","earphones","headphones","headset",
    "speaker","soundbar","projector","camera","monitor","tv",
    "router","printer","vacuum","air","coffee",
    "synthesizer","synth","eurorack","modular","midi",
}

COMPATIBILITY_ONLY_TOKENS = {
    "headphones","earphones","earbuds","headset",
    "phone","iphone","tablet","ipad","laptop","macbook","samsung",
    "xiaomi","huawei","book","kindle","reader",
}

def has_device_intent(keyword: str) -> bool:
    k_tokens = set(tokenize_search_text(keyword))
    if k_tokens & DEVICE_INTENT_TERMS:
        return True
    return contains_any_phrase(normalize_search_text(keyword), STRONG_DEVICE_PHRASES)

def classify_product_title(
    title: str,
    price: Optional[float] = None,
    keyword: Optional[str] = None,
) -> Dict:
    title_norm = normalize_search_text(title)
    main_product_name = normalize_search_text(extract_main_product_name(title))
    main_tokens = get_title_tokens(main_product_name)
    tokens = get_title_tokens(title_norm)
    positive_hits: List[str] = []
    negative_hits: List[str] = []
    score = 0.0

    if not title_norm:
        return {
            "is_device": False, "score": -999.0,
            "positive_hits": [], "negative_hits": ["empty_title"],
            "tokens": set(), "reason": "empty title", "main_product": "",
        }

    bag_tokens = {"backpack","bag","pouch","satchel","tote","sleeve"}
    if main_tokens & bag_tokens:
        score -= 6.0
        negative_hits.append("main_product_is_bag_or_backpack")

    if "backpack" in tokens:
        score -= 5.0
        negative_hits.append("backpack_in_title")

    for phrase in STRONG_DEVICE_PHRASES:
        if phrase in main_product_name:
            score += 3.0
            positive_hits.append(phrase)

    for phrase in ACCESSORY_PHRASES:
        if phrase in title_norm:
            score -= 4.0
            negative_hits.append(phrase)

    for tok in main_tokens:
        if tok in DEVICE_TOKENS:
            score += 1.0
            positive_hits.append(tok)
        if tok in ACCESSORY_TOKENS:
            score -= 1.5
            negative_hits.append(tok)
        if tok in DEVICE_CONTEXT_HINTS:
            score += 0.5
        if tok in ACCESSORY_CONTEXT_HINTS:
            score -= 0.5

    compatibility_part = title_norm[len(main_product_name):]
    compat_tokens = get_title_tokens(compatibility_part)
    for tok in compat_tokens:
        if tok in ACCESSORY_TOKENS:
            score -= 1.0
            negative_hits.append(f"compat:{tok}")
        if tok in COMPATIBILITY_ONLY_TOKENS and tok not in main_tokens:
            score -= 0.5
            negative_hits.append(f"compat_only:{tok}")

    if {"eurorack","modular","synthesizer"} & main_tokens:
        score += 2.5
        positive_hits.append("music_device_context")

    if "speaker" in main_tokens and ({"bluetooth","wireless","desktop","gaming","stereo","hifi"} & main_tokens):
        score += 1.5
        positive_hits.append("speaker_context")

    if "case" in main_tokens and not ({"eurorack","modular","rack","flight"} & main_tokens):
        if main_tokens & {"phone","iphone","tablet","ipad","laptop","macbook","watch"}:
            score -= 2.0
            negative_hits.append("generic_case_accessory")

    if "cover" in main_tokens and main_tokens & {"phone","iphone","tablet","ipad","watch","laptop","macbook"}:
        score -= 2.0
        negative_hits.append("generic_cover_accessory")

    if main_tokens & {"charger","cable","adapter","dock"}:
        score -= 2.5
        negative_hits.append("charging_or_adapter_accessory")

    if "replacement" in main_tokens or "repair" in main_tokens or "spare" in main_tokens:
        score -= 2.5
        negative_hits.append("replacement_or_repair_accessory")

    if price is not None:
        if price < 3.0:
            score -= 3.0
            negative_hits.append("very_low_price")
        elif price < 8.0:
            score -= 1.5
            negative_hits.append("low_price")
        elif price >= 20.0:
            score += 0.5

    if keyword:
        kw_norm = normalize_search_text(keyword)
        kw_tokens = set(tokenize_search_text(keyword))
        overlap = main_tokens & kw_tokens
        if overlap:
            score += min(2.0, 0.5 * len(overlap))
            positive_hits.append("keyword_overlap")
        if contains_any_phrase(kw_norm, STRONG_DEVICE_PHRASES) and contains_any_phrase(main_product_name, STRONG_DEVICE_PHRASES):
            score += 1.0
            positive_hits.append("strong_phrase_keyword_alignment")
        if has_device_intent(keyword) and (main_tokens & DEVICE_TOKENS):
            score += 0.5
            positive_hits.append("device_intent_alignment")

    device_token_hits = len(main_tokens & DEVICE_TOKENS)
    strong_accessory = any(p in title_norm for p in ACCESSORY_PHRASES)

    is_device = (
        score >= 2.0
        and device_token_hits >= 1
        and not (strong_accessory and score < 3.0)
        and "backpack_in_title" not in negative_hits
        and "main_product_is_bag_or_backpack" not in negative_hits
    )

    return {
        "is_device": is_device,
        "score": round(score, 2),
        "positive_hits": positive_hits,
        "negative_hits": negative_hits,
        "tokens": tokens,
        "main_product": main_product_name,
        "reason": f"score={round(score,2)} main={main_product_name[:40]}",
    }

def is_real_device_title(title: str, price: Optional[float] = None) -> bool:
    return bool(classify_product_title(title=title, price=price)["is_device"])

def is_shipping_safe(price: float, original_price: Optional[float] = None) -> bool:
    if price is None:
        return False
    if price < 3.0:
        return False
    if original_price and original_price > 0:
        if (original_price - price) / original_price > 0.95:
            return False
    return True

def is_relevant_product(title: str, keyword: str) -> bool:
    title_norm = normalize_search_text(title)
    keyword_norm = normalize_search_text(keyword)
    if not title_norm:
        return False
    if not keyword_norm:
        return True
    main_name = normalize_search_text(extract_main_product_name(title))
    main_tokens = get_title_tokens(main_name)
    keyword_tokens = set(tokenize_search_text(keyword_norm))
    if not keyword_tokens:
        return True
    overlap = main_tokens & keyword_tokens
    required_matches = 1 if len(keyword_tokens) <= 2 else 2
    if len(overlap) >= required_matches:
        return True
    full_tokens = get_title_tokens(title_norm)
    full_overlap = full_tokens & keyword_tokens
    if len(full_overlap) >= required_matches and len(overlap) == 0:
        return False
    if has_device_intent(keyword):
        cls = classify_product_title(title=title_norm, keyword=keyword_norm)
        if cls["is_device"] and len(overlap) >= 1:
            return True
    return False

def load_last_deals() -> Dict[str, Dict]:
    if not os.path.exists(LAST_DEALS_FILE):
        return {}
    try:
        with open(LAST_DEALS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}

def save_last_deals(data: Dict[str, Dict]) -> None:
    try:
        with open(LAST_DEALS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Could not save last_deals.json:", e)

def remember_deals(products: List[Dict]) -> None:
    if not products:
        return
    last_deals = load_last_deals()
    now_iso = datetime.utcnow().isoformat()
    for p in products:
        pid = str(p.get("product_id") or p.get("productid") or p.get("productId") or p.get("id") or "")
        if not pid:
            continue
        last_deals[pid] = {"product_id": pid, "timestamp": now_iso}
    save_last_deals(last_deals)

def filter_old_and_duplicates(products: List[Dict]) -> List[Dict]:
    if not products:
        return products
    last_deals = load_last_deals()
    if not last_deals:
        return products
    cutoff = datetime.utcnow() - timedelta(days=LAST_DEALS_MAX_AGE_DAYS)
    valid_ids: Set[str] = set()
    cleaned: Dict[str, Dict] = {}
    for pid, info in last_deals.items():
        try:
            ts = datetime.fromisoformat(info.get("timestamp"))
            if ts >= cutoff:
                cleaned[pid] = info
                valid_ids.add(pid)
        except Exception:
            continue
    save_last_deals(cleaned)
    filtered: List[Dict] = []
    for p in products:
        pid = str(p.get("product_id") or p.get("productid") or p.get("productId") or p.get("id") or "")
        if not pid or pid in valid_ids:
            continue
        filtered.append(p)
    return filtered

def to_float(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        v = str(value).replace("US$","").replace("US $","").replace(",","").strip()
        return float(v)
    except Exception:
        return None

def compute_discount_pct(old_price: Optional[float], new_price: Optional[float]) -> Optional[float]:
    if not old_price or not new_price or old_price <= 0:
        return None
    return round(100.0 * (old_price - new_price) / old_price, 2)

def normalize_product_item(item: Dict) -> Dict:
    title = item.get("product_title") or item.get("title") or "Product"
    detail_url = item.get("product_detail_url") or item.get("url")
    image_url = item.get("product_main_image_url") or item.get("image_url")
    old_price = to_float(item.get("original_price") or item.get("target_original_price") or item.get("sale_price"))
    new_price = to_float(item.get("app_sale_price") or item.get("target_app_sale_price") or item.get("target_sale_price") or item.get("sale_price"))
    discount_pct = compute_discount_pct(old_price, new_price)
    orders = item.get("sales_volume") or item.get("orders") or 0
    try:
        orders = int(float(orders))
    except Exception:
        orders = 0
    rating = to_float(item.get("evaluate_score") or item.get("average_rating"))
    product_id = item.get("product_id") or item.get("productId")
    main_product = extract_main_product_name(title)
    return {
        "product_id": product_id, "productid": product_id,
        "title": title, "product_title": title,
        "main_product_name": main_product,
        "product_detail_url": detail_url, "productdetailurl": detail_url,
        "image_url": image_url, "imageurl": image_url,
        "product_main_image_url": image_url, "productMainImageUrl": image_url,
        "old_price": old_price, "oldprice": old_price,
        "new_price": new_price, "newprice": new_price,
        "discount_pct": discount_pct, "discountpct": discount_pct,
        "orders": orders, "rating": rating,
        "affiliate_link": None, "affiliatelink": None,
    }

def generate_affiliate_link(
    source_url: str,
    tracking_id: str = DEFAULT_TRACKING_ID,
    ship_to_country: str = "FR",
    promotion_link_type: str = "0",
) -> Optional[str]:
    method = "aliexpress.affiliate.link.generate"
    params = {
        "source_values": source_url,
        "tracking_id": tracking_id,
        "promotion_link_type": promotion_link_type,
        "ship_to_country": ship_to_country,
    }
    data = ali.call_api_top(method, params)
    if not isinstance(data, dict) or not is_api_ok(data):
        return None
    try:
        result = parse_api_result(data, method) or {}
        links = result.get("promotion_links") or result.get("promotionLinks") or []
        if not links:
            return None
        first = links[0]
        return first.get("promotion_link") or first.get("promotionLink")
    except Exception:
        return None

def generate_affiliate_links_batch(
    product_urls: List[str],
    tracking_id: str = DEFAULT_TRACKING_ID,
    ship_to_country: str = "FR",
) -> Dict[str, Optional[str]]:
    if not product_urls:
        return {}
    method = "aliexpress.affiliate.link.generate"
    params = {
        "source_values": ",".join(product_urls[:50]),
        "tracking_id": tracking_id,
        "promotion_link_type": "0",
        "ship_to_country": ship_to_country,
    }
    data = ali.call_api_top(method, params)
    url_map: Dict[str, Optional[str]] = {}
    if not isinstance(data, dict) or not is_api_ok(data):
        return url_map
    try:
        result = parse_api_result(data, method) or {}
        links = result.get("promotion_links") or result.get("promotionLinks") or []
        for link_obj in links:
            source = link_obj.get("source_value")
            promo = link_obj.get("promotion_link") or link_obj.get("promotionLink")
            if source and promo:
                url_map[source] = promo
    except Exception:
        pass
    return url_map

def search_products(
    keyword: str,
    country: str = "FR",
    page_no: int = 1,
    page_size: int = 50,
    min_sale_price: Optional[float] = None,
    max_sale_price: Optional[float] = None,
) -> List[Dict]:
    method = "aliexpress.affiliate.product.query"
    params = {
        "keywords": keyword,
        "page_no": str(page_no),
        "page_size": str(page_size),
        "target_currency": "USD",
        "target_language": "EN",
        "ship_to_country": country,
        "fields": "product_id,product_title,original_price,app_sale_price,discount,sales_volume,product_main_image_url,product_detail_url,evaluate_score",
        "tracking_id": DEFAULT_TRACKING_ID,
    }
    if min_sale_price is not None:
        params["min_sale_price"] = str(min_sale_price)
    if max_sale_price is not None:
        params["max_sale_price"] = str(max_sale_price)
    data = ali.call_api_top(method, params)
    if not isinstance(data, dict) or not is_api_ok(data):
        return []
    try:
        result = parse_api_result(data, method) or {}
        return result.get("products") or result.get("result_list") or result.get("product_list") or []
    except Exception:
        return []

def get_hot_products(
    country: str = "FR",
    keywords: Optional[str] = None,
    category_ids: Optional[str] = None,
    page_no: int = 1,
    page_size: int = 50,
    sort: str = "LAST_VOLUME_DESC",
    min_sale_price: Optional[float] = None,
    max_sale_price: Optional[float] = None,
) -> List[Dict]:
    method = "aliexpress.affiliate.hotproduct.query"
    params = {
        "page_no": str(page_no),
        "page_size": str(page_size),
        "target_currency": "USD",
        "target_language": "EN",
        "country": country,
        "fields": "product_id,product_title,original_price,app_sale_price,discount,sales_volume,product_main_image_url,product_detail_url,evaluate_score",
        "tracking_id": DEFAULT_TRACKING_ID,
        "sort": sort,
    }
    if keywords:
        params["keywords"] = keywords
    if category_ids:
        params["category_ids"] = category_ids
    if min_sale_price is not None:
        params["min_sale_price"] = str(min_sale_price)
    if max_sale_price is not None:
        params["max_sale_price"] = str(max_sale_price)
    data = ali.call_api_top(method, params)
    if not isinstance(data, dict) or not is_api_ok(data):
        return []
    try:
        result = parse_api_result(data, method) or {}
        return result.get("products") or result.get("result_list") or result.get("product_list") or []
    except Exception:
        return []

def normalize_value(value: float, max_value: float) -> float:
    if max_value <= 0:
        return 0.0
    return min(1.0, max(0.0, float(value)) / max_value)

def calculate_deal_score_with_context(item: Dict, max_orders: float, max_discount: float) -> float:
    orders = item.get("orders") or 0
    rating = item.get("rating") or 0.0
    discount_pct = item.get("discount_pct") or item.get("discountpct") or 0.0
    norm_sales = normalize_value(orders, max_orders) if max_orders > 0 else 0.0
    norm_rating = normalize_value(rating, 5.0)
    norm_discount = normalize_value(discount_pct, max_discount) if max_discount > 0 else 0.0
    return round((norm_sales * 0.4) + (norm_rating * 0.3) + (norm_discount * 0.3), 4)

def choose_best_and_cheapest_offer(products_raw: List[Dict]) -> Tuple[Optional[Dict], Optional[Dict]]:
    if not products_raw:
        return None, None
    normalized = [p for p in [normalize_product_item(x) for x in products_raw] if p.get("title")]
    if not normalized:
        return None, None
    priced = [p for p in normalized if p.get("new_price") is not None]
    max_orders = max((p.get("orders") or 0) for p in normalized) or 0
    max_discount = max((p.get("discount_pct") or 0.0) for p in normalized) or 0.0
    best_offer = sorted(normalized, key=lambda p: calculate_deal_score_with_context(p, max_orders, max_discount), reverse=True)[0]
    cheapest_offer: Optional[Dict] = None
    if priced:
        candidates = [p for p in priced if (p.get("rating") or 0.0) >= 4.0 and p.get("product_id") != best_offer.get("product_id")]
        if candidates:
            cheapest_offer = sorted(candidates, key=lambda p: p["new_price"])[0]
        elif len(priced) > 1:
            fb = sorted(priced, key=lambda p: p["new_price"])[0]
            if fb.get("product_id") != best_offer.get("product_id"):
                cheapest_offer = fb
    return best_offer, cheapest_offer

def search_best_and_cheapest_with_affiliate_links(
    keyword: str,
    country: str = "FR",
    tracking_id: str = DEFAULT_TRACKING_ID,
    min_sale_price: Optional[float] = None,
    max_sale_price: Optional[float] = None,
) -> Tuple[Optional[Dict], Optional[Dict]]:
    products_raw = search_products(keyword, country=country, page_no=1, page_size=50, min_sale_price=min_sale_price, max_sale_price=max_sale_price)
    if not products_raw:
        products_raw = get_hot_products(country=country, keywords=keyword, page_no=1, page_size=50, sort="LAST_VOLUME_DESC", min_sale_price=min_sale_price, max_sale_price=max_sale_price)
    if not products_raw:
        return None, None
    scored = []
    for item in products_raw:
        title = item.get("product_title") or item.get("title") or ""
        price = to_float(item.get("app_sale_price") or item.get("sale_price"))
        if classify_product_title(title=title, price=price, keyword=keyword)["is_device"]:
            scored.append(item)
    if not scored and has_device_intent(keyword):
        return None, None
    source_list = scored if scored else products_raw
    relevant = [item for item in source_list if is_relevant_product(item.get("product_title") or item.get("title") or "", keyword)]
    final_source = relevant if relevant else source_list
    if not final_source:
        return None, None
    best, cheapest = choose_best_and_cheapest_offer(final_source)
    def attach(prod):
        if not prod or not prod.get("product_detail_url"):
            return None
        aff = generate_affiliate_link(source_url=prod["product_detail_url"], tracking_id=tracking_id, ship_to_country=country)
        if not aff:
            return None
        prod["affiliate_link"] = aff
        prod["affiliatelink"] = aff
        return prod
    return attach(best), attach(cheapest)

def get_daily_hot_deals_with_affiliate_links(
    country: str = "FR",
    tracking_id: str = DEFAULT_TRACKING_ID,
    limit: int = 3,
    keywords: Optional[str] = None,
    category_ids: Optional[str] = None,
    min_sale_price: Optional[float] = None,
    max_sale_price: Optional[float] = None,
) -> List[Dict]:
    products_raw = get_hot_products(country=country, keywords=keywords, category_ids=category_ids, page_no=1, page_size=50, sort="LAST_VOLUME_DESC", min_sale_price=min_sale_price, max_sale_price=max_sale_price)
    if not products_raw:
        return []
    normalized = [normalize_product_item(p) for p in products_raw]
    filtered_quality = [
        p for p in normalized
        if (p.get("rating") or 0.0) >= 4.2
        and (p.get("orders") or 0) >= 50
        and is_shipping_safe(p.get("new_price"), p.get("old_price"))
    ]
    candidates = filtered_quality if filtered_quality else normalized
    device_candidates = []
    for p in candidates:
        cls = classify_product_title(title=p.get("title") or "", price=p.get("new_price"), keyword=keywords or "")
        if cls["is_device"]:
            p["_classification_score"] = cls["score"]
            p["main_product_name"] = cls.get("main_product", p.get("title",""))
            device_candidates.append(p)
    if not device_candidates:
        return []
    filtered_unique = filter_old_and_duplicates(device_candidates) or device_candidates
    max_orders = max((p.get("orders") or 0) for p in filtered_unique) or 0
    max_discount = max((p.get("discount_pct") or 0.0) for p in filtered_unique) or 0.0
    def final_score(p):
        ds = calculate_deal_score_with_context(p, max_orders, max_discount)
        cs = float(p.get("_classification_score") or 0.0)
        return round((ds * 0.75) + (min(cs, 6.0) / 6.0 * 0.25), 4)
    top_products = sorted(filtered_unique, key=final_score, reverse=True)[:limit]
    if not top_products:
        return []
    urls = [p["product_detail_url"] for p in top_products if p.get("product_detail_url")]
    affiliate_map = generate_affiliate_links_batch(urls, tracking_id, country)
    final_deals = []
    for p in top_products:
        aff = affiliate_map.get(p.get("product_detail_url"))
        if aff:
            p["affiliate_link"] = aff
            p["affiliatelink"] = aff
            p["deal_type"] = "AliExpress"
            p.pop("_classification_score", None)
            final_deals.append(p)
    remember_deals(final_deals)
    return final_deals

def extract_product_id_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    for pattern in [r"/item/(\d+)\.html", r"[?&]productId=(\d+)", r"(\d{6,})"]:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None

def get_product_details_from_url(
    source_url: str,
    country: str = DEFAULT_COUNTRY,
    target_currency: str = "USD",
    tracking_id: str = DEFAULT_TRACKING_ID,
) -> Optional[Dict]:
    product_id = extract_product_id_from_url(source_url)
    if not product_id:
        return None
    method = "aliexpress.affiliate.productdetail.get"
    params = {"product_ids": product_id, "target_currency": target_currency, "target_language": "EN", "tracking_id": tracking_id}
    data = ali.call_api_top(method, params)
    if not isinstance(data, dict) or not is_api_ok(data):
        return None
    try:
        result = parse_api_result(data, method) or {}
        products = result.get("products") or result.get("product_list") or result.get("result_list") or []
        item = products if isinstance(products, dict) else (products[0] if isinstance(products, list) and products else None)
        if not item:
            return None
        product = normalize_product_item(item)
        detail_url = product.get("product_detail_url") or source_url
        aff = generate_affiliate_link(source_url=detail_url, tracking_id=tracking_id, ship_to_country=country)
        if aff:
            product["affiliate_link"] = aff
            product["affiliatelink"] = aff
        product["deal_type"] = "AliExpress"
        return product
    except Exception:
        return None

def generate_search_variations(original_keyword: str, max_variations: int = 5) -> List[str]:
    orig  = original_keyword.lower().strip()
    words = orig.split()
    variations: List[str] = []

    # أزل أرقام الموديل (mt46, x200, pro3...)
    clean = re.sub(r'\b[a-z]*\d+[a-z]*\b', '', orig)
    clean = re.sub(r'\s+', ' ', clean).strip()
    if clean and clean != orig and len(clean) > 3:
        variations.append(clean)

    # أول 3 كلمات
    if len(words) > 3:
        variations.append(" ".join(words[:3]))

    # أول 2 كلمات
    if len(words) > 2:
        variations.append(" ".join(words[:2]))

    # أول كلمة + آخر كلمة
    if len(words) > 2:
        variations.append(f"{words[0]} {words[-1]}")

    # آخر كلمة فقط (الأكثر عمومية)
    if len(words) > 1:
        variations.append(words[-1])

    seen: List[str] = []
    for v in variations:
        v = v.strip()
        if v and v != orig and v not in seen and len(v) > 2:
            seen.append(v)
    return seen[:max_variations]

def search_with_retry(
    keyword: str,
    country: str = "FR",
    max_retries: int = 3,
    page_size: int = 50,
    min_sale_price: Optional[float] = None,
    max_sale_price: Optional[float] = None,
) -> Tuple[List[Dict], str]:
    all_keywords = [keyword] + generate_search_variations(keyword, max_variations=5)
    for attempt, kw in enumerate(all_keywords):
        products = search_products(kw, country, 1, page_size, min_sale_price, max_sale_price)
        if products:
            return products, kw
        if attempt == len(all_keywords) - 1:
            hot = get_hot_products(
                country, kw,
                page_no=1, page_size=page_size,
                sort="LAST_VOLUME_DESC",
                min_sale_price=min_sale_price,
                max_sale_price=max_sale_price,
            )
            if hot:
                return hot, f"{kw} [hot]"
    return [], keyword

def search_best_and_cheapest_with_retry(
    keyword: str,
    country: str = "FR",
    tracking_id: str = DEFAULT_TRACKING_ID,
    max_retries: int = 3,
    min_sale_price: Optional[float] = None,
    max_sale_price: Optional[float] = None,
) -> Tuple[Optional[Dict], Optional[Dict], str]:
    products_raw, keyword_used = search_with_retry(keyword=keyword, country=country, max_retries=max_retries, min_sale_price=min_sale_price, max_sale_price=max_sale_price)
    if not products_raw:
        return None, None, keyword_used
    scored = []
    for item in products_raw:
        title = item.get("product_title") or item.get("title") or ""
        price = to_float(item.get("app_sale_price") or item.get("sale_price"))
        if classify_product_title(title=title, price=price, keyword=keyword_used)["is_device"]:
            scored.append(item)
    if not scored and has_device_intent(keyword):
        return None, None, keyword_used
    source_list = scored if scored else products_raw
    relevant = [item for item in source_list if is_relevant_product(item.get("product_title") or item.get("title") or "", keyword_used)]
    final_source = relevant if relevant else source_list
    if not final_source:
        return None, None, keyword_used
    best, cheapest = choose_best_and_cheapest_offer(final_source)
    def attach(prod):
        if not prod or not prod.get("product_detail_url"):
            return None
        aff = generate_affiliate_link(source_url=prod["product_detail_url"], tracking_id=tracking_id, ship_to_country=country)
        if not aff:
            return None
        prod["affiliate_link"] = aff
        prod["affiliatelink"] = aff
        return prod
    return attach(best), attach(cheapest), keyword_used
