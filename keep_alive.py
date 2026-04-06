from flask import Flask, request, redirect, jsonify, send_file
from threading import Thread
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from typing import Dict, Any, Optional, Tuple
import os
import json
import ipaddress
import re
import io
import html

from affiliate_helpers import (
    search_best_and_cheapest_with_affiliate_links,
    generate_affiliate_link,
)
from utils import (
    format_best_deal_message,
    format_cheapest_deal_message,
    send_to_make_webhook,
)

app = Flask(__name__)

CLICKS_FILE = os.getenv("CLICKS_FILE", "clicks.jsonl").strip()
ALLOWED_REDIRECT_HOSTS = os.getenv("ALLOWED_REDIRECT_HOSTS", "").strip()
ADMIN_KEY = os.getenv("ADMIN_KEY", "mysecret123").strip()
GEOLOOKUP_ENABLED = os.getenv("GEOLOOKUP_ENABLED", "true").strip().lower() in (
    "1", "true", "yes", "on"
)
GEOLOOKUP_TIMEOUT = float(os.getenv("GEOLOOKUP_TIMEOUT", "2.5"))
FREEIPAPI_BASE = os.getenv("FREEIPAPI_BASE", "https://free.freeipapi.com/api/json").strip()

_GEO_CACHE = {}

BOT_USER_AGENTS = re.compile(
    r"(bot|crawler|spider|facebookexternalhit|whatsapp|telegrambot|tiktok|metauri|discordbot|slurp|twitterbot)",
    re.IGNORECASE,
)


def is_bot(user_agent: str) -> bool:
    if not user_agent:
        return False
    return bool(BOT_USER_AGENTS.search(user_agent))


def append_click(row: dict) -> None:
    with open(CLICKS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def is_allowed_destination(dest: str) -> bool:
    if not ALLOWED_REDIRECT_HOSTS:
        return True
    allowed_hosts = [
        x.strip().lower()
        for x in ALLOWED_REDIRECT_HOSTS.split(",")
        if x.strip()
    ]
    parsed = urlparse(dest)
    host = (parsed.netloc or "").lower()
    if not host:
        return False
    return any(host == allowed or host.endswith("." + allowed) for allowed in allowed_hosts)


def normalize_text(value: str, default: str = "unknown") -> str:
    value = (value or "").strip()
    return value if value else default


def get_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    return (request.remote_addr or "").strip() or "unknown"


def is_public_ip(ip: str) -> bool:
    try:
        obj = ipaddress.ip_address(ip)
        return not (
            obj.is_private
            or obj.is_loopback
            or obj.is_link_local
            or obj.is_multicast
            or obj.is_reserved
            or obj.is_unspecified
        )
    except Exception:
        return False


def geo_lookup(ip: str) -> dict:
    if not GEOLOOKUP_ENABLED:
        return {"country": "unknown", "region": "unknown"}
    ip = (ip or "").strip()
    if not ip or ip == "unknown" or not is_public_ip(ip):
        return {"country": "unknown", "region": "unknown"}
    if ip in _GEO_CACHE:
        return _GEO_CACHE[ip]
    url = f"{FREEIPAPI_BASE}/{ip}"
    req = Request(url, headers={"User-Agent": "affiliate-click-tracker/1.0"})
    try:
        with urlopen(req, timeout=GEOLOOKUP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        country = (
            data.get("countryName")
            or data.get("country")
            or data.get("country_name")
            or "unknown"
        )
        region = (
            data.get("regionName")
            or data.get("region")
            or data.get("stateName")
            or data.get("region_name")
            or "unknown"
        )
        result = {
            "country": normalize_text(country),
            "region": normalize_text(region),
        }
    except (URLError, HTTPError, TimeoutError, ValueError, json.JSONDecodeError):
        result = {"country": "unknown", "region": "unknown"}
    except Exception:
        result = {"country": "unknown", "region": "unknown"}
    _GEO_CACHE[ip] = result
    return result


def make_unique_key(row: dict) -> str:
    ip = normalize_text(row.get("ip", "unknown"))
    platform = normalize_text(row.get("platform", "unknown"))
    return f"{ip}|{platform}"


def to_float_or_none(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("%", "").strip())
    except Exception:
        return None


def append_open_bot_link_to_html(msg: str) -> str:
    base = (os.getenv("BOT_LINK") or "").strip()
    if not base:
        username = (os.getenv("BOT_USERNAME") or "").strip()
        if username.startswith("@"):
            username = username[1:]
        if username:
            base = f"https://t.me/{username}"
    if not base or base.endswith("t.me/"):
        return msg or ""
    deep = f"{base}?start=from_channel" if "?" not in base else base
    if deep in (msg or ""):
        return msg or ""
    safe_url = html.escape(deep, quote=True)
    return (msg or "") + f'\n\n<a href="{safe_url}">🤖 افتح البوت</a>'


def get_channel_meta() -> Dict[str, str]:
    raw_username = (
        os.getenv("CHANNEL_USERNAME") or os.getenv("TELEGRAM_CHANNEL_ID") or ""
    ).strip()
    channel_username = raw_username
    channel_link = (os.getenv("CHANNEL_LINK") or "").strip()
    channel_id = (
        os.getenv("CHANNEL_ID") or os.getenv("TELEGRAM_CHANNEL_ID") or channel_username
    ).strip()
    if not channel_link and channel_username and not channel_username.startswith("-100"):
        uname = channel_username.lstrip("@")
        channel_link = f"https://t.me/{uname}"
    return {
        "channel_id": channel_id,
        "channel_username": channel_username,
        "channel_link": channel_link,
        "bot_link": (os.getenv("BOT_LINK") or "").strip(),
    }


def pick_deal(
    best: Optional[Dict[str, Any]],
    cheapest: Optional[Dict[str, Any]],
    prefer: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    prefer = (prefer or "best").strip().lower()
    if prefer == "cheapest":
        if cheapest:
            return cheapest, "cheapest"
        if best:
            return best, "best"
        return None, None
    if best:
        return best, "best"
    if cheapest:
        return cheapest, "cheapest"
    return None, None


def ensure_affiliate_link(deal: Dict[str, Any], country: str) -> Dict[str, Any]:
    existing = (
        deal.get("affiliate_link")
        or deal.get("affiliatelink")
        or deal.get("affiliate_url")
        or deal.get("affiliateurl")
    )
    if existing:
        if not deal.get("affiliate_link"):
            deal["affiliate_link"] = existing
        if not deal.get("affiliatelink"):
            deal["affiliatelink"] = existing
        return deal
    source_url = (
        deal.get("product_detail_url")
        or deal.get("productdetailurl")
        or deal.get("productDetailUrl")
        or ""
    ).strip()
    if not source_url:
        return deal
    aff = generate_affiliate_link(source_url=source_url, ship_to_country=country)
    if aff:
        deal["affiliate_link"] = aff
        deal["affiliatelink"] = aff
    return deal


def normalize_for_templates(deal: Dict[str, Any]) -> Dict[str, Any]:
    title = (
        deal.get("title")
        or deal.get("product_title")
        or deal.get("producttitle")
        or deal.get("productTitle")
        or "Product"
    )
    old_price = (
        deal.get("old_price")
        or deal.get("oldprice")
        or deal.get("target_original_price")
        or deal.get("targetoriginalprice")
        or deal.get("original_price")
        or deal.get("originalprice")
    )
    new_price = (
        deal.get("new_price")
        or deal.get("newprice")
        or deal.get("target_sale_price")
        or deal.get("targetsaleprice")
        or deal.get("sale_price")
        or deal.get("saleprice")
    )
    discount_raw = (
        deal.get("discount_pct")
        or deal.get("discountpct")
        or deal.get("discount")
    )
    discount_pct = to_float_or_none(discount_raw)
    rating = deal.get("rating") or deal.get("evaluate_score") or deal.get("evaluatescore")
    orders = (
        deal.get("orders")
        or deal.get("sales_volume")
        or deal.get("salesvolume")
        or deal.get("sale_num")
        or deal.get("salenum")
    )
    affiliate_link = (
        deal.get("affiliate_link")
        or deal.get("affiliatelink")
        or deal.get("affiliate_url")
        or deal.get("affiliateurl")
        or deal.get("product_detail_url")
        or deal.get("productdetailurl")
    )
    image_url = (
        deal.get("image_url")
        or deal.get("imageurl")
        or deal.get("product_main_image_url")
        or deal.get("productmainimageurl")
        or deal.get("productMainImageUrl")
    )
    currency = (
        deal.get("currency")
        or deal.get("target_sale_price_currency")
        or deal.get("targetsalepricecurrency")
        or "USD"
    )
    return {
        "title": title,
        "old_price": old_price,
        "new_price": new_price,
        "discount_pct": discount_pct,
        "rating": rating,
        "orders": orders,
        "affiliate_link": affiliate_link,
        "affiliatelink": affiliate_link,
        "image_url": image_url,
        "imageurl": image_url,
        "currency": currency,
    }


def build_publish_payload(
    chosen: Dict[str, Any],
    picked: str,
    country: str,
    mode: str,
    keyword: str,
) -> Dict[str, Any]:
    chosen = ensure_affiliate_link(chosen, country)
    normalized = normalize_for_templates(chosen)
    if picked == "cheapest":
        msg, image_url = format_cheapest_deal_message(normalized, country=country)
    else:
        msg, image_url = format_best_deal_message(normalized, country=country)
    payload = {
        "source": "telegram_bot",
        "mode": mode,
        "msg": append_open_bot_link_to_html(msg),
        "image_url": image_url,
        "deal": chosen,
        "country": country,
        "keyword": keyword,
        "picked": picked,
    }
    payload.update(get_channel_meta())
    return payload


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────

@app.route("/")
def home():
    return jsonify({
        "ok": True,
        "service": "affiliate-click-tracker",
        "health": "/health",
        "stats": "/stats",
        "search_product": "/search-product",
        "search_and_publish": "/search-and-publish",
        "ali_auth": "/ali-auth-start",
        "track_example": "/go?platform=facebook&product_type=deal&product_category=smartwatch&campaign=main_scenario&dest=https%3A%2F%2Fexample.com",
    })


@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "clicks_file": CLICKS_FILE,
        "allowed_redirect_hosts": ALLOWED_REDIRECT_HOSTS,
        "geo_enabled": GEOLOOKUP_ENABLED,
        "make_webhook_configured": bool(os.getenv("MAKE_WEBHOOK_URL", "").strip()),
    })


@app.route("/search-product", methods=["POST"])
def search_product():
    try:
        data = request.get_json(silent=True) or {}
        keyword = (data.get("keyword") or "").strip()
        country = (data.get("country") or "DZ").strip().upper()
        min_sale_price = to_float_or_none(data.get("min_sale_price"))
        max_sale_price = to_float_or_none(data.get("max_sale_price"))
        if not keyword:
            return jsonify({"ok": False, "error": "Missing keyword"}), 400
        best, cheapest = search_best_and_cheapest_with_affiliate_links(
            keyword=keyword,
            country=country,
            min_sale_price=min_sale_price,
            max_sale_price=max_sale_price,
        )
        if not best and not cheapest:
            return jsonify({
                "ok": False,
                "error": "No products found",
                "keyword": keyword,
                "country": country,
            }), 404
        chosen = best or cheapest
        return jsonify({
            "ok": True,
            "keyword": keyword,
            "country": country,
            "deal": chosen,
            "best": best,
            "cheapest": cheapest,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/search-and-publish", methods=["POST"])
def search_and_publish():
    try:
        data = request.get_json(silent=True) or {}
        keyword = (data.get("keyword") or "").strip()
        country = (data.get("country") or "DZ").strip().upper()
        prefer = (data.get("prefer") or "best").strip().lower()
        mode = (data.get("mode") or "daily_auto").strip()
        min_sale_price = to_float_or_none(data.get("min_sale_price"))
        max_sale_price = to_float_or_none(data.get("max_sale_price"))
        if not keyword:
            return jsonify({
                "ok": False, "found": False, "published": False, "error": "Missing keyword"
            }), 400
        best, cheapest = search_best_and_cheapest_with_affiliate_links(
            keyword=keyword,
            country=country,
            min_sale_price=min_sale_price,
            max_sale_price=max_sale_price,
        )
        if not best and not cheapest:
            return jsonify({
                "ok": False, "found": False, "published": False,
                "keyword": keyword, "country": country, "error": "No products found",
            }), 200
        chosen, picked = pick_deal(best, cheapest, prefer)
        if not chosen or not picked:
            return jsonify({
                "ok": False, "found": False, "published": False,
                "keyword": keyword, "country": country, "error": "Unable to pick a deal",
            }), 200
        chosen = ensure_affiliate_link(chosen, country)
        payload = build_publish_payload(
            chosen=chosen, picked=picked, country=country, mode=mode, keyword=keyword,
        )
        sent = bool(send_to_make_webhook(payload))
        return jsonify({
            "ok": sent, "found": True, "published": sent,
            "keyword": keyword, "country": country, "picked": picked,
            "deal": chosen, "image_url": payload.get("image_url"), "msg": payload.get("msg"),
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "found": False, "published": False, "error": str(e)}), 500


@app.route("/go")
def go():
    platform = normalize_text(request.args.get("platform", "unknown"))
    product_type = normalize_text(request.args.get("product_type", request.args.get("content_type", "unknown")))
    product_category = normalize_text(request.args.get("product_category", "unknown"))
    content_id = normalize_text(request.args.get("content_id", "unknown"))
    campaign = normalize_text(request.args.get("campaign", "main"))
    dest = unquote(request.args.get("dest", "")).strip()
    user_agent = request.headers.get("User-Agent", "")
    if not dest:
        return jsonify({"ok": False, "error": "Missing dest"}), 400
    if not (dest.startswith("http://") or dest.startswith("https://")):
        return jsonify({"ok": False, "error": "Invalid destination URL"}), 400
    if not is_allowed_destination(dest):
        return jsonify({"ok": False, "error": "Destination not allowed"}), 400
    if is_bot(user_agent):
        return redirect(dest, code=302)
    ip = get_client_ip()
    geo = geo_lookup(ip)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "platform": platform,
        "product_type": product_type,
        "product_category": product_category,
        "content_id": content_id,
        "campaign": campaign,
        "country": normalize_text(geo.get("country", "unknown")),
        "region": normalize_text(geo.get("region", "unknown")),
        "ip": ip,
        "dest": dest,
    }
    try:
        append_click(row)
    except Exception as e:
        print("append_click error:", e)
    return redirect(dest, code=302)


@app.route("/stats")
def stats():
    rows = []
    try:
        if os.path.exists(CLICKS_FILE):
            with open(CLICKS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
        total_clicks = len(rows)
        seen_unique = set()
        unique_clicks = 0
        repeat_clicks = 0
        clicks_by_platform = {}
        clicks_by_product_type = {}
        clicks_by_product_category = {}
        clicks_by_country = {}
        clicks_by_region = {}
        clicks_by_campaign = {}
        recent_clicks = []
        for row in rows:
            platform = normalize_text(row.get("platform", "unknown"))
            product_type = normalize_text(row.get("product_type", row.get("content_type", "unknown")))
            product_category = normalize_text(row.get("product_category", "unknown"))
            country = normalize_text(row.get("country", "unknown"))
            region = normalize_text(row.get("region", "unknown"))
            campaign = normalize_text(row.get("campaign", "main"))
            clicks_by_platform[platform] = clicks_by_platform.get(platform, 0) + 1
            clicks_by_product_type[product_type] = clicks_by_product_type.get(product_type, 0) + 1
            clicks_by_product_category[product_category] = clicks_by_product_category.get(product_category, 0) + 1
            clicks_by_country[country] = clicks_by_country.get(country, 0) + 1
            clicks_by_region[region] = clicks_by_region.get(region, 0) + 1
            clicks_by_campaign[campaign] = clicks_by_campaign.get(campaign, 0) + 1
            unique_key = make_unique_key(row)
            if unique_key in seen_unique:
                repeat_clicks += 1
                click_kind = "repeat"
            else:
                seen_unique.add(unique_key)
                unique_clicks += 1
                click_kind = "unique"
            recent_clicks.append({
                "ts": row.get("ts"),
                "platform": platform,
                "product_type": product_type,
                "product_category": product_category,
                "country": country,
                "region": region,
                "campaign": campaign,
                "content_id": normalize_text(row.get("content_id", "unknown")),
                "click_kind": click_kind,
            })
        recent_clicks = recent_clicks[-50:][::-1]
        return jsonify({
            "total_clicks": total_clicks,
            "unique_clicks": unique_clicks,
            "repeat_clicks": repeat_clicks,
            "clicks_by_platform": dict(sorted(clicks_by_platform.items(), key=lambda x: x[1], reverse=True)),
            "clicks_by_product_type": dict(sorted(clicks_by_product_type.items(), key=lambda x: x[1], reverse=True)),
            "clicks_by_product_category": dict(sorted(clicks_by_product_category.items(), key=lambda x: x[1], reverse=True)),
            "clicks_by_country": dict(sorted(clicks_by_country.items(), key=lambda x: x[1], reverse=True)),
            "clicks_by_region": dict(sorted(clicks_by_region.items(), key=lambda x: x[1], reverse=True)),
            "clicks_by_campaign": dict(sorted(clicks_by_campaign.items(), key=lambda x: x[1], reverse=True)),
            "recent_clicks": recent_clicks,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/export-stats")
def export_stats():
    key = request.args.get("key", "")
    if key != ADMIN_KEY:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    if not os.path.exists(CLICKS_FILE):
        return jsonify({"ok": False, "error": "No data to export"}), 404
    with open(CLICKS_FILE, "rb") as f:
        file_content = f.read()
    if len(file_content) == 0:
        return jsonify({"ok": False, "error": "File is already empty"}), 404
    memory_file = io.BytesIO(file_content)
    open(CLICKS_FILE, "w").close()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    download_name = f"clicks_backup_{timestamp}.jsonl"
    return send_file(
        memory_file,
        as_attachment=True,
        download_name=download_name,
        mimetype="application/jsonl",
    )


# ─────────────────────────────────────────────
# ✅ AliExpress Auth Routes (Token Renewal)
# ─────────────────────────────────────────────

@app.route("/ali-auth-start")
def ali_auth_start():
    from ali_api import APP_KEY
    redirect_uri = "https://replit.com/@oualidmsirdi/AffiliateExpress"
    oauth_url = (
        "https://api-sg.aliexpress.com/oauth/authorize"
        f"?response_type=code"
        f"&client_id={APP_KEY}"
        f"&redirect_uri={redirect_uri}"
        f"&view=web&sp=ae"
    )
    return f"""
    <html>
    <body style="font-family:Arial,sans-serif;padding:30px;direction:rtl;max-width:700px;margin:auto">
        <h2>🔑 تجديد Token الخاص بـ AliExpress</h2>
        <hr>
        <h3>الخطوة 1: سجّل الدخول</h3>
        <a href="{oauth_url}" target="_blank"
           style="display:inline-block;padding:12px 24px;background:#e74c3c;color:white;
                  text-decoration:none;border-radius:6px;font-size:16px">
            👉 اضغط هنا لتسجيل الدخول في AliExpress
        </a>
        <hr>
        <h3>الخطوة 2: انسخ الـ CODE</h3>
        <p>بعد تسجيل الدخول، ستُحوَّل إلى رابط مثل:</p>
        <code style="background:#f0f0f0;padding:8px;display:block;word-break:break-all">
            https://replit.com/...?code=<b>XXXXXXXX</b>&sp=ae&...
        </code>
        <p>انسخ فقط الجزء بعد <b>code=</b> (قبل علامة & التالية)</p>
        <hr>
        <h3>الخطوة 3: افتح هذا الرابط مع الكود</h3>
        <code style="background:#f0f0f0;padding:8px;display:block;word-break:break-all">
            https://aliexpress-telegram-bot-1-gi2v.onrender.com/ali-auth-callback?code=ضع-الكود-هنا
        </code>
    </body>
    </html>
    """


@app.route("/ali-auth-callback")
def ali_auth_callback():
    import requests as http
    from ali_api import AliExpress, build_timestamp_ms, build_signature, BASE_REST_URL, API_AUTH_CREATE

    code = request.args.get("code", "").strip()
    if not code:
        return jsonify({"ok": False, "error": "No code provided. Add ?code=YOUR_CODE to the URL"}), 400

    ali = AliExpress()
    params = {
        "app_key": ali.app_key,
        "timestamp": build_timestamp_ms(),
        "sign_method": "sha256",
        "code": code,
    }
    params["sign"] = build_signature(params, API_AUTH_CREATE, ali.app_secret)

    resp = http.get(BASE_REST_URL + API_AUTH_CREATE, params=params, timeout=30)
    data = resp.json()

    if "access_token" in data:
        return jsonify({
            "ok": True,
            "SUCCESS": "✅ انسخ الـ tokens التالية وضعهما في Render → Environment",
            "ALI_ACCESS_TOKEN":  data["access_token"],
            "ALI_REFRESH_TOKEN": data.get("refresh_token", ""),
            "expires_in_days":         round(int(data.get("expires_in", 0)) / 86400),
            "refresh_expires_in_days": round(int(data.get("refresh_expires_in", 0)) / 86400),
        })
    return jsonify({"ok": False, "raw_response": data})


# ─────────────────────────────────────────────
# Server startup
# ─────────────────────────────────────────────

def run():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    t = Thread(target=run)
    t.start()
