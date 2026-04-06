import asyncio
import logging
import os
import requests
from typing import Dict, Any, Optional, Tuple, List
from datetime import time as dtime

from dotenv import load_dotenv
from deep_translator import GoogleTranslator

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from affiliate_helpers import (
    search_best_and_cheapest_with_affiliate_links,
    get_daily_hot_deals_with_affiliate_links,
    generate_affiliate_link,
    get_product_details_from_url,
    DEVICE_KEYWORDS,
)

from utils import (
    format_deal_message,
    format_best_deal_message,
    format_cheapest_deal_message,
    send_to_make_webhook,
)

from search_stats import record_search, get_top_searches
from keep_alive import keep_alive
from trends import get_shopping_trends, get_shopping_trends_data

# ---------------------------------------------------------
# Load config
# ---------------------------------------------------------
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
DEFAULT_COUNTRY = os.getenv("DEFAULT_COUNTRY", "FR").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "5031866102"))

TRENDS_MODE_DEFAULT = os.getenv("TRENDS_MODE_DEFAULT", "manual").strip().lower()
if TRENDS_MODE_DEFAULT not in ("manual", "auto"):
    TRENDS_MODE_DEFAULT = "manual"

SEND_SCHEDULED_TRENDS_PREVIEW_TO_OWNER = (
    os.getenv("SEND_SCHEDULED_TRENDS_PREVIEW_TO_OWNER", "false").strip().lower()
    in ("1", "true", "yes", "on")
)

MAX_TG_MESSAGE_LEN = 3500
TRENDWEBHOOKURL = os.getenv("TRENDWEBHOOKURL", "").strip()

# ---------------------------------------------------------
# Channel / Bot Link
# ---------------------------------------------------------
raw_username = (os.getenv("CHANNEL_USERNAME") or os.getenv("TELEGRAM_CHANNEL_ID") or "").strip()
CHANNEL_USERNAME = raw_username
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "").strip()
CHANNEL_ID = (os.getenv("CHANNEL_ID") or os.getenv("TELEGRAM_CHANNEL_ID") or CHANNEL_USERNAME).strip()

if not CHANNEL_LINK and CHANNEL_USERNAME and not CHANNEL_USERNAME.startswith("-100"):
    uname = CHANNEL_USERNAME.lstrip("@")
    CHANNEL_LINK = f"https://t.me/{uname}"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# ---------------------------------------------------------
# Async wrappers for blocking code
# ---------------------------------------------------------
async def run_blocking(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)

async def translate_to_english_async(text: str) -> str:
    return await run_blocking(translate_to_english, text)

async def record_search_async(*args, **kwargs):
    return await run_blocking(record_search, *args, **kwargs)

async def get_top_searches_async(*args, **kwargs):
    return await run_blocking(get_top_searches, *args, **kwargs)

async def search_best_and_cheapest_async(*args, **kwargs):
    return await run_blocking(search_best_and_cheapest_with_affiliate_links, *args, **kwargs)

async def get_daily_hot_deals_async(*args, **kwargs):
    return await run_blocking(get_daily_hot_deals_with_affiliate_links, *args, **kwargs)

async def generate_affiliate_link_async(*args, **kwargs):
    return await run_blocking(generate_affiliate_link, *args, **kwargs)

async def get_product_details_async(*args, **kwargs):
    return await run_blocking(get_product_details_from_url, *args, **kwargs)

async def get_shopping_trends_async(*args, **kwargs):
    return await run_blocking(get_shopping_trends, *args, **kwargs)

async def get_shopping_trends_data_async(*args, **kwargs):
    return await run_blocking(get_shopping_trends_data, *args, **kwargs)

async def send_to_make_webhook_async(payload: Dict[str, Any]) -> bool:
    return bool(await run_blocking(send_to_make_webhook, payload))

def _post_json_sync(url: str, payload: dict, timeout: int = 20):
    return requests.post(url, json=payload, timeout=timeout)

async def post_json_async(url: str, payload: dict, timeout: int = 20):
    return await run_blocking(_post_json_sync, url, payload, timeout)

# ---------------------------------------------------------
# Helper: Open Bot Button / Link
# ---------------------------------------------------------
def get_open_bot_keyboard(
    bot_username: str = "",
    bot_link: str = "",
    start_param: str = "from_channel",
) -> InlineKeyboardMarkup:
    link = (bot_link or os.getenv("BOT_LINK", "") or "").strip()

    if not link:
        u = (bot_username or os.getenv("BOT_USERNAME", "") or "").strip()
        if u.startswith("@"):
            u = u[1:]
        if u:
            link = f"https://t.me/{u}"

    if not link or link.endswith("t.me/"):
        return InlineKeyboardMarkup([])

    if "t.me" in link and "?" not in link and start_param:
        link = f"{link}?start={start_param}"

    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛍️ Search More Deals in Bot", url=link)]]
    )

def append_open_bot_link_to_html(msg: str) -> str:
    msg = msg or ""
    base = (os.getenv("BOT_LINK") or "").strip()

    if not base:
        u = (os.getenv("BOT_USERNAME") or "").strip()
        if u.startswith("@"):
            u = u[1:]
        if u:
            base = f"https://t.me/{u}"

    if not base or base.endswith("t.me/"):
        return msg

    deep = f"{base}?start=from_channel" if "?" not in base else base
    if deep in msg:
        return msg

    return msg + f"\n\n👉 افتح البوت: {deep}"

# ---------------------------------------------------------
# Language & Translation
# ---------------------------------------------------------
def is_arabic_text(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06FF" for ch in (text or ""))

def translate_to_english(text: str) -> str:
    original = (text or "").strip()
    if not original:
        return original

    improvements = {
        "ساعة يد ذكية": "smart watch",
        "ساعة ذكية مقاومة للماء للرياضة": "smart watch waterproof sport",
        "ساعه ذكيه مقاومه للماء للرياضه": "smart watch waterproof sport",
        "سماعات أذن لاسلكية بلوتوث": "wireless bluetooth earbuds",
        "سماعات اذن بلوتوث لاسلكية 5.3": "true wireless earbuds bluetooth 5.3",
        "سماعات لاسلكية": "wireless bluetooth earbuds",
        "سماعة بلوتوث": "bluetooth earphone",
        "شاحن سيارة سريع usb c": "car charger fast usb c",
        "محول شاحن سريع usb c": "fast charger usb c adapter",
        "كابل شحن سريع usb c": "fast charging usb c cable",
        "هاتف ذكي اندرويد 5g": "android smartphone 5g",
        "جوال ذكي 5g": "android smartphone 5g",
        "حقيبة يد نسائية": "women handbag",
        "ادوات مطبخ منزلية": "home gadgets kitchen tools",
        "أدوات مطبخ منزليه": "home gadgets kitchen tools",
    }

    for ar_phrase, en_phrase in improvements.items():
        if ar_phrase in original:
            return en_phrase

    try:
        translator = GoogleTranslator(source="auto", target="en")
        translated = translator.translate(original.strip())
        return translated
    except Exception as e:
        logging.error(f"Translation Error: {e}")
        return original

def extract_url_and_title(text: str) -> Tuple[Optional[str], str]:
    if not text:
        return None, ""
    parts = text.split(maxsplit=1)
    url = parts[0].strip()
    title = parts[1].strip() if len(parts) > 1 else ""
    return url, title

def is_url(s: str) -> bool:
    s = (s or "").strip().lower()
    return s.startswith("http://") or s.startswith("https://")

# ---------------------------------------------------------
# Channel Check
# ---------------------------------------------------------
async def is_channel_member(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not CHANNEL_USERNAME:
        return True
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except BadRequest:
        return False
    except Exception as e:
        logging.error(f"get_chat_member error: {e}")
        return False

async def ensure_channel_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not CHANNEL_USERNAME or not CHANNEL_LINK:
        return True

    user_id = update.effective_user.id
    if await is_channel_member(user_id, context):
        return True

    text = (
        f"عذرًا، للاستفادة من البوت، يرجى الاشتراك في القناة أولاً:\n{CHANNEL_LINK}\n"
        f"ثم اضغط على زر التحقق."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 اشترك الآن", url=CHANNEL_LINK)],
        [InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="check_join")],
    ])
    await update.effective_message.reply_text(text, reply_markup=keyboard)
    return False

# ---------------------------------------------------------
# Templates / Normalization
# ---------------------------------------------------------
def normalize_for_templates(deal: Dict[str, Any]) -> Dict[str, Any]:
    title = deal.get("title") or deal.get("product_title") or "Product"
    old_price = (
        deal.get("old_price")
        or deal.get("oldprice")
        or deal.get("target_original_price")
        or deal.get("original_price")
    )
    new_price = (
        deal.get("new_price")
        or deal.get("newprice")
        or deal.get("target_sale_price")
        or deal.get("sale_price")
    )
    discount_raw = deal.get("discount_pct") or deal.get("discountpct") or deal.get("discount")
    discount_pct = None
    if isinstance(discount_raw, (int, float)):
        discount_pct = float(discount_raw)
    elif isinstance(discount_raw, str):
        try:
            discount_pct = float(discount_raw.replace("%", "").strip())
        except Exception:
            discount_pct = None

    rating = deal.get("rating") or deal.get("evaluate_score")
    orders = deal.get("orders") or deal.get("sales_volume") or deal.get("sale_num")
    affiliate_link = (
        deal.get("affiliate_link")
        or deal.get("affiliatelink")
        or deal.get("affiliate_url")
        or deal.get("product_detail_url")
        or deal.get("productdetailurl")
    )
    image_url = (
        deal.get("image_url")
        or deal.get("imageurl")
        or deal.get("product_main_image_url")
        or deal.get("productMainImageUrl")
    )
    return {
        "title": title,
        "old_price": old_price,
        "new_price": new_price,
        "discount_pct": discount_pct,
        "rating": rating,
        "orders": orders,
        "affiliate_link": affiliate_link,
        "image_url": image_url,
    }

# ---------------------------------------------------------
# Keyboards
# ---------------------------------------------------------
def get_post_actions_keyboard(is_owner: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔥 أحدث العروض", callback_data="cmd_daily_deals"),
            InlineKeyboardButton("📱 عروض التقنية", callback_data="cmd_device_deals"),
        ],
        [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="menu_main")],
    ])

def get_main_menu_keyboard(is_owner: bool = False) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🔍 ابحث عن منتج بنفسك", callback_data="cmd_search_help")],
        [InlineKeyboardButton("📁 تصفح المنتجات حسب الفئة", callback_data="menu_catalog")],
        [
            InlineKeyboardButton("🔥 أحدث العروض القوية", callback_data="cmd_daily_deals"),
            InlineKeyboardButton("📱 عروض التقنية", callback_data="cmd_device_deals"),
        ],
        [
            InlineKeyboardButton("📈 أكثر المنتجات بحثاً", callback_data="cmd_top_searches"),
            InlineKeyboardButton("🌍 تغيير بلد الشحن", callback_data="menu_country"),
        ],
    ]
    if is_owner:
        keyboard.append([InlineKeyboardButton("📊 عرض الترند لبلد الشحن", callback_data="cmd_trends")])
        keyboard.append([InlineKeyboardButton("🛠 تحويل رابط لأفلييت", callback_data="cmd_link_help")])
        keyboard.append([InlineKeyboardButton("📢 نشر عرض بالقناة (آلي)", callback_data="cmd_owner_post_deals")])
        keyboard.append([InlineKeyboardButton("📝 نشر رابط مخصص بالقناة", callback_data="cmd_manual_post")])
    return InlineKeyboardMarkup(keyboard)

def get_country_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("الجزائر (DZ)", callback_data="set_country_DZ"),
            InlineKeyboardButton("المغرب (MA)", callback_data="set_country_MA"),
        ],
        [
            InlineKeyboardButton("تونس (TN)", callback_data="set_country_TN"),
            InlineKeyboardButton("فرنسا (FR)", callback_data="set_country_FR"),
        ],
        [
            InlineKeyboardButton("كندا (CA)", callback_data="set_country_CA"),
            InlineKeyboardButton("أمريكا (US)", callback_data="set_country_US"),
        ],
        [InlineKeyboardButton("🔙 رجوع", callback_data="menu_main")],
    ])

def get_catalog_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📱 هواتف ذكية", callback_data="search_smartphones"),
            InlineKeyboardButton("⌚ ساعات ذكية", callback_data="search_smartwatch"),
        ],
        [
            InlineKeyboardButton("🎧 سماعات أذن", callback_data="search_earbuds"),
            InlineKeyboardButton("🏠 أجهزة منزلية", callback_data="search_homegadgets"),
        ],
        [
            InlineKeyboardButton("👟 حقائب وأحذية", callback_data="search_bagsshoes"),
            InlineKeyboardButton("💄 مكياج وعناية", callback_data="search_makeupbeauty"),
        ],
        [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="menu_main")],
    ])

def get_trends_mode(context: ContextTypes.DEFAULT_TYPE) -> str:
    mode = (context.user_data.get("trends_mode") or TRENDS_MODE_DEFAULT or "manual").strip().lower()
    if mode not in ("manual", "auto"):
        mode = "manual"
    return mode

def get_trends_actions_keyboard(mode: str) -> InlineKeyboardMarkup:
    mode = (mode or "manual").strip().lower()
    if mode == "manual":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 إرسال القائمة إلى السيناريو", callback_data="trend_send_scenario")],
            [InlineKeyboardButton("⚙️ التحويل إلى الوضع التلقائي", callback_data="trend_mode_auto")],
            [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="menu_main")],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ التحويل إلى الوضع اليدوي", callback_data="trend_mode_manual")],
        [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="menu_main")],
    ])

# ---------------------------------------------------------
# Message Splitting Helpers
# ---------------------------------------------------------
def split_long_message(text: str, max_len: int = MAX_TG_MESSAGE_LEN) -> List[str]:
    text = text or ""
    if len(text) <= max_len:
        return [text]

    chunks: List[str] = []
    current = ""

    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > max_len and current:
            chunks.append(current)
            current = line
        else:
            current += line

    if current:
        chunks.append(current)

    final_chunks: List[str] = []
    for chunk in chunks:
        if len(chunk) <= max_len:
            final_chunks.append(chunk)
        else:
            for i in range(0, len(chunk), max_len):
                final_chunks.append(chunk[i:i + max_len])

    return final_chunks or [text[:max_len]]

async def send_long_html_message(
    message_obj,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    chunks = split_long_message(text)
    for idx, chunk in enumerate(chunks):
        rm = reply_markup if idx == len(chunks) - 1 else None
        await message_obj.reply_text(chunk, parse_mode="HTML", reply_markup=rm)

async def send_long_html_to_chat(
    bot,
    chat_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    chunks = split_long_message(text)
    for idx, chunk in enumerate(chunks):
        rm = reply_markup if idx == len(chunks) - 1 else None
        await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML", reply_markup=rm)

# ---------------------------------------------------------
# Webhook Helpers
# ---------------------------------------------------------
async def send_post_to_scenario(
    mode: str,
    msg: str,
    image_url: Optional[str],
    deal: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> bool:
    deal = deal or {}
    payload = {
        "source": "telegram_bot",
        "mode": mode,
        "msg": append_open_bot_link_to_html(msg),
        "image_url": image_url,
        "deal": deal,
        "country": DEFAULT_COUNTRY,
        "channel_id": CHANNEL_ID,
        "channel_username": CHANNEL_USERNAME,
        "channel_link": CHANNEL_LINK,
        "bot_link": (os.getenv("BOT_LINK") or "").strip(),
        "platform": deal.get("platform", "tiktok"),
        "bucket": deal.get("bucket", "explore"),
        "category_group": deal.get("category_group", "_unknown"),
        "fb_template": deal.get("fb_template", "direct"),
    }
    if extra:
        payload.update(extra)
    return await send_to_make_webhook_async(payload)

async def build_trends_payload(country: str, trend_msg: str, source_kind: str) -> Dict[str, Any]:
    trends_data = await get_shopping_trends_data_async(country_code=country)
    return {
        "deal_type": "trends",
        "content_type": "trends",
        "product_type": "trends",
        "trend_source": "google_shopping",
        "trend_country": country,
        "source_kind": source_kind,
        "preview": (trend_msg or "")[:1200],
        "trends_count": len(trends_data),
        "trends": trends_data,
    }

async def send_trends_to_scenario(country: str, trend_msg: str, mode: str, source_kind: str) -> bool:
    if not TRENDWEBHOOKURL:
        logging.error("TRENDWEBHOOKURL is missing")
        return False

    deal = await build_trends_payload(country=country, trend_msg=trend_msg, source_kind=source_kind)
    payload = {
        "source": "telegram_bot",
        "mode": mode,
        "msg": trend_msg,
        "image_url": None,
        "deal": deal,
        "country": country,
        "channel_id": CHANNEL_ID,
        "channel_username": CHANNEL_USERNAME,
        "channel_link": CHANNEL_LINK,
        "bot_link": (os.getenv("BOT_LINK") or "").strip(),
        "content_type": "trends",
        "product_type": "trends",
        "trend_country": country,
        "trend_source": "google_shopping",
        "source_kind": source_kind,
        "trends_count": deal.get("trends_count", 0),
        "trends": deal.get("trends", []),
    }
    try:
        response = await post_json_async(TRENDWEBHOOKURL, payload, timeout=20)
        ok = 200 <= response.status_code < 300
        if not ok:
            logging.error(f"Trend webhook error: {response.status_code} - {response.text}")
        return ok
    except Exception as e:
        logging.error(f"send_trends_to_scenario error: {e}")
        return False

# ---------------------------------------------------------
# Generic send helpers
# ---------------------------------------------------------
async def safe_reply_deal(
    message_obj,
    text: str,
    img: Optional[str],
    reply_markup: InlineKeyboardMarkup,
    parse_mode: str = "HTML",
):
    if img:
        try:
            await message_obj.reply_photo(
                photo=img,
                caption=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            return
        except Exception as e:
            logging.error(f"reply_photo failed, fallback to text: {e}")
    await message_obj.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)

# ---------------------------------------------------------
# Search Logic
# ---------------------------------------------------------
async def perform_search_logic(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    raw_keyword: str,
    country: str,
):
    if not await ensure_channel_member(update, context):
        return

    message_obj = update.callback_query.message if update.callback_query else update.message
    final_keyword = (raw_keyword or "").strip()
    translation_note = ""

    if is_arabic_text(final_keyword):
        translated = await translate_to_english_async(final_keyword)
        if translated and translated.lower() != final_keyword.lower():
            translation_note = f"\n(تم ترجمة '{final_keyword}' إلى '{translated}')"
            final_keyword = translated

    try:
        await record_search_async(
            country=country,
            raw_keyword=raw_keyword,
            translated_keyword=final_keyword,
        )
    except Exception as e:
        logging.error(f"record_search error: {e}")

    status_msg = await message_obj.reply_text(
        f"🔍 جاري البحث عن أفضل عروض لـ: {final_keyword}...{translation_note}"
    )
    try:
        best, cheapest = await search_best_and_cheapest_async(final_keyword, country=country)

        try:
            await status_msg.delete()
        except Exception:
            pass

        if not best and not cheapest:
            await message_obj.reply_text(
                f"لم أجد نتائج جيدة لـ '{final_keyword}'. جرب كلمات أخرى.",
                reply_markup=get_post_actions_keyboard(),
            )
            return

        if best:
            best_norm = normalize_for_templates(best)
            text, img = format_best_deal_message(best_norm, country=country)
            await safe_reply_deal(
                message_obj=message_obj,
                text=text,
                img=img,
                reply_markup=get_post_actions_keyboard(),
            )

        if cheapest:
            cheapest_norm = normalize_for_templates(cheapest)
            text, img = format_cheapest_deal_message(cheapest_norm, country=country)
            await safe_reply_deal(
                message_obj=message_obj,
                text=text,
                img=img,
                reply_markup=get_post_actions_keyboard(),
            )

    except Exception as e:
        logging.error(f"Search Error: {e}")
        try:
            await status_msg.edit_text("حدث خطأ أثناء البحث، يرجى المحاولة لاحقاً.")
        except Exception:
            pass

# ---------------------------------------------------------
# Daily Deal / Job Logic
# ---------------------------------------------------------
async def post_one_deal_to_channel(
    context: ContextTypes.DEFAULT_TYPE,
    country: str = DEFAULT_COUNTRY,
    platform: str = "tiktok",
) -> bool:
    try:
        deals = await get_daily_hot_deals_async(
            country=country,
            limit=1,
            platform=platform,
        )
        if not deals:
            return False

        deal = deals[0]
        msg = format_deal_message(deal)
        image_url = (
            deal.get("image_url")
            or deal.get("product_main_image_url")
            or deal.get("productMainImageUrl")
        )

        ok = await send_post_to_scenario(
            mode="daily_auto",
            msg=msg,
            image_url=image_url,
            deal=deal,
            extra={"country": country},
        )

        if ok:
            return True

        fallback_enabled = (
            os.getenv("FALLBACK_DAILY_TO_CHANNEL", "true").strip().lower()
            in ("1", "true", "yes", "on")
        )
        if not fallback_enabled:
            return False
        if not CHANNEL_ID:
            return False

        me = await context.bot.get_me()
        kb = get_open_bot_keyboard(bot_username=getattr(me, "username", ""))

        if image_url:
            await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=image_url,
                caption=msg,
                parse_mode="HTML",
                reply_markup=kb,
            )
        else:
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=msg,
                parse_mode="HTML",
                reply_markup=kb,
            )
        return True

    except Exception as e:
        logging.error(f"Error posting daily deal (scenario/fallback): {e}")
        return False

async def scheduled_daily_deal(context: ContextTypes.DEFAULT_TYPE):
    await post_one_deal_to_channel(context, country=DEFAULT_COUNTRY, platform="tiktok")

# ---------------------------------------------------------
# Trends Logic
# ---------------------------------------------------------
async def handle_trends_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_obj = update.callback_query.message if update.callback_query else update.message
    user_country = context.user_data.get("country", DEFAULT_COUNTRY)
    trends_mode = get_trends_mode(context)

    await message_obj.reply_text(f"⏳ جاري سحب الترند من جوجل ({user_country})...")

    try:
        trend_msg = await get_shopping_trends_async(user_country)
    except Exception as e:
        logging.error(f"get_shopping_trends error: {e}")
        await message_obj.reply_text("❌ حدث خطأ أثناء جلب الترند.")
        return

    trend_msg = (trend_msg or "").strip()
    if not trend_msg:
        await message_obj.reply_text("❌ لم تصل بيانات ترند صالحة.")
        return

    context.user_data["pending_trends"] = {
        "msg": trend_msg,
        "country": user_country,
    }

    if trends_mode == "auto":
        ok = await send_trends_to_scenario(
            country=user_country,
            trend_msg=trend_msg,
            mode="trends_manual_auto",
            source_kind="manual_request_auto_mode",
        )
        await send_long_html_message(
            message_obj,
            trend_msg,
            reply_markup=get_trends_actions_keyboard("auto"),
        )
        await message_obj.reply_text(
            "✅ تم إرسال القائمة إلى السيناريو تلقائيًا."
            if ok
            else "❌ فشل إرسال القائمة إلى السيناريو تلقائيًا."
        )
    else:
        await send_long_html_message(
            message_obj,
            trend_msg,
            reply_markup=get_trends_actions_keyboard("manual"),
        )

async def scheduled_trends(context: ContextTypes.DEFAULT_TYPE):
    country = DEFAULT_COUNTRY
    try:
        trend_msg = await get_shopping_trends_async(country)
        ok = await send_trends_to_scenario(
            country=country,
            trend_msg=trend_msg,
            mode="trends_scheduled_auto",
            source_kind="scheduled_job",
        )
        status_text = (
            f"✅ تم إرسال الترند الصباحي ({country}) إلى السيناريو."
            if ok
            else f"❌ فشل إرسال الترند الصباحي ({country}) إلى السيناريو."
        )
        try:
            await context.bot.send_message(chat_id=OWNER_ID, text=status_text)
        except Exception as e:
            logging.error(f"Failed to notify owner for scheduled trends: {e}")

        if SEND_SCHEDULED_TRENDS_PREVIEW_TO_OWNER:
            try:
                await send_long_html_to_chat(
                    context.bot,
                    OWNER_ID,
                    f"🌞 الترند الصباحي ({country})\n\n{trend_msg}",
                    reply_markup=get_trends_actions_keyboard("auto"),
                )
            except Exception as e:
                logging.error(f"Failed to send trends preview to owner: {e}")

    except Exception as e:
        logging.error(f"Failed to run scheduled trends: {e}")
        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=f"❌ حدث خطأ أثناء تشغيل الترند الصباحي ({country}).",
            )
        except Exception:
            pass

# ---------------------------------------------------------
# Commands
# ---------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_firstname = update.effective_user.first_name or "صديقي"
    is_owner = update.effective_user.id == OWNER_ID
    text = (
        f"أهلاً {user_firstname}! 👋\n"
        f"أنا مساعدك الشخصي للبحث عن أفضل العروض والتخفيضات من AliExpress.\n"
        f"اختر ما تريد من القائمة:"
    )
    await update.message.reply_text(text, reply_markup=get_main_menu_keyboard(is_owner=is_owner))

async def admin_prepare_post_from_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    source_url: str,
    custom_title: str,
) -> None:
    status = await update.effective_message.reply_text("⏳ جاري تحويل الرابط واستخراج البيانات...")
    try:
        aff = await generate_affiliate_link_async(
            source_url=source_url,
            ship_to_country=DEFAULT_COUNTRY,
        )
        product = await get_product_details_async(
            source_url=source_url,
            country=DEFAULT_COUNTRY,
        ) or {}

        if custom_title:
            product["title"] = custom_title
        if aff:
            product["affiliate_link"] = aff
        if not product.get("title"):
            product["title"] = custom_title or "منتج AliExpress"

        if not product.get("affiliate_link"):
            await status.edit_text("❌ لم أتمكن من تحويل الرابط إلى أفلييت.")
            return

        product["deal_type"] = "AliExpress"
        msg = format_deal_message(product)
        image_url = (
            product.get("image_url")
            or product.get("product_main_image_url")
            or product.get("productMainImageUrl")
        )

        ok = await send_post_to_scenario(
            mode="manual_preview",
            msg=msg,
            image_url=image_url,
            deal=product,
            extra={"source_url": source_url},
        )
        try:
            await status.delete()
        except Exception:
            pass

        if ok:
            await update.effective_message.reply_text("✅ Sent to Make webhook successfully.")
        else:
            await update.effective_message.reply_text("❌ فشل الإرسال إلى Make Webhook.")

    except Exception as e:
        logging.error(f"admin_prepare_post_from_url error: {e}")
        await status.edit_text("❌ حدث خطأ أثناء جلب التفاصيل.")

def build_devices_only_keywords_from_big_list() -> str:
    allow_tokens = [
        "smartphone", "phone", "mobile", "android phone", "iphone",
        "smart watch", "smartwatch",
        "earbuds", "earphones", "headphones", "headset",
        "projector",
    ]
    picked = []
    for kw in DEVICE_KEYWORDS:
        k = (kw or "").strip().lower()
        if any(tok in k for tok in allow_tokens):
            picked.append(k)
    picked.append("mini projector")

    uniq = []
    seen = set()
    for k in picked:
        if k and k not in seen:
            uniq.append(k)
            seen.add(k)
    return ",".join(uniq)

# ---------------------------------------------------------
# Callback Query Handler
# ---------------------------------------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    data = query.data
    user_country = context.user_data.get("country", DEFAULT_COUNTRY)
    user_id = query.from_user.id
    is_owner = user_id == OWNER_ID

    if data == "menu_main":
        await query.message.reply_text(
            "القائمة الرئيسية:",
            reply_markup=get_main_menu_keyboard(is_owner=is_owner),
        )
        try:
            await query.delete_message()
        except Exception:
            pass

    elif data == "menu_country":
        await query.edit_message_text(
            f"بلد الشحن الحالي: {user_country}\nاختر البلد الجديد:",
            reply_markup=get_country_keyboard(),
        )

    elif data == "menu_catalog":
        await query.edit_message_text(
            "تصفح المنتجات حسب الفئة:",
            reply_markup=get_catalog_keyboard(),
        )

    elif data.startswith("set_country_"):
        new_country = data.split("_")[-1]
        context.user_data["country"] = new_country
        await query.edit_message_text(
            f"تم تغيير البلد إلى: {new_country}",
            reply_markup=get_main_menu_keyboard(is_owner=is_owner),
        )

    elif data.startswith("search_"):
        keyword = data.split("_", 1)[1].replace("_", " ")
        await perform_search_logic(update, context, keyword, user_country)

    elif data == "cmd_search_help":
        text = "للبحث عن منتج، أرسل اسمه مباشرة هنا في الدردشة.\nمثال: `ساعة ذكية` أو `search iphone`"
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 رجوع", callback_data="menu_main")]]
            ),
        )

    elif data == "cmd_daily_deals":
        if not await ensure_channel_member(update, context):
            return
        await query.message.reply_text("⏳ جلب أفضل التخفيضات...")
        try:
            deals = await get_daily_hot_deals_async(
                limit=3,
                country=user_country,
                platform="tiktok",
            )
            if not deals:
                await query.message.reply_text(
                    "لم أجد عروض قوية اليوم.",
                    reply_markup=get_post_actions_keyboard(is_owner=is_owner),
                )
            else:
                for deal in deals:
                    msg = format_deal_message(deal)
                    image_url = (
                        deal.get("image_url")
                        or deal.get("product_main_image_url")
                        or deal.get("productMainImageUrl")
                    )
                    await safe_reply_deal(
                        message_obj=query.message,
                        text=msg,
                        img=image_url,
                        reply_markup=get_post_actions_keyboard(is_owner=is_owner),
                    )
        except Exception as e:
            logging.error(f"Daily deals error: {e}")
            await query.message.reply_text(
                "حدث خطأ.",
                reply_markup=get_post_actions_keyboard(is_owner=is_owner),
            )

    elif data == "cmd_device_deals":
        if not await ensure_channel_member(update, context):
            return
        await query.message.reply_text("⏳ جلب عروض الأجهزة...")
        try:
            kw = build_devices_only_keywords_from_big_list()
            trials = [{"min_sale_price": 20.0}, {"min_sale_price": 15.0}, {"min_sale_price": None}]
            deals = []
            for t in trials:
                deals = await get_daily_hot_deals_async(
                    limit=3,
                    country=user_country,
                    keywords=kw,
                    min_sale_price=t["min_sale_price"],
                    platform="tiktok",
                )
                if deals:
                    break

            if not deals:
                await query.message.reply_text(
                    "لم أجد عروض تقنية حالياً. جرب لاحقاً.",
                    reply_markup=get_post_actions_keyboard(is_owner=is_owner),
                )
            else:
                for deal in deals:
                    msg = format_deal_message(deal)
                    image_url = (
                        deal.get("image_url")
                        or deal.get("product_main_image_url")
                        or deal.get("productMainImageUrl")
                    )
                    await safe_reply_deal(
                        message_obj=query.message,
                        text=msg,
                        img=image_url,
                        reply_markup=get_post_actions_keyboard(is_owner=is_owner),
                    )
        except Exception as e:
            logging.error(f"Device deals error: {e}")
            await query.message.reply_text(
                "حدث خطأ.",
                reply_markup=get_post_actions_keyboard(is_owner=is_owner),
            )

    elif data == "cmd_link_help":
        if not is_owner:
            return
        context.user_data["awaiting_affiliate_post"] = True
        await query.message.reply_text(
            "أرسل رابط AliExpress لتحويله.\nمثال:\nhttps://www.aliexpress.com/item/123..."
        )

    elif data == "cmd_top_searches":
        if not await ensure_channel_member(update, context):
            return
        top = await get_top_searches_async(user_country, limit=5)
        if not top:
            await query.message.reply_text(
                "لا توجد بيانات بحث كافية بعد.",
                reply_markup=get_post_actions_keyboard(is_owner=is_owner),
            )
        else:
            lines = [f"📈 أعلى الكلمات بحثاً ({user_country}):\n"]
            for i, (kw0, count) in enumerate(top, start=1):
                lines.append(f"{i}. {kw0} ({count} مرات)")
            await query.message.reply_text(
                "\n".join(lines),
                reply_markup=get_post_actions_keyboard(is_owner=is_owner),
            )

    elif data == "cmd_owner_post_deals":
        if not is_owner:
            return
        await query.message.reply_text("⏳ جاري نشر عرض آلي...")
        ok = await post_one_deal_to_channel(context, country=DEFAULT_COUNTRY, platform="tiktok")
        await query.message.reply_text("✅ تم الإرسال للويبهوك." if ok else "❌ فشل النشر.")

    elif data == "cmd_manual_post":
        if not is_owner:
            return
        context.user_data["awaiting_manual_post"] = True
        await query.message.reply_text("أرسل رابط AliExpress لنشره مخصصاً.")

    elif data == "cmd_trends":
        if not is_owner:
            return
        await handle_trends_request(update, context)

    elif data == "trend_send_scenario":
        if not is_owner:
            return
        pending = context.user_data.get("pending_trends")
        if not pending:
            await query.message.reply_text("❌ لا توجد قائمة ترند محفوظة للإرسال.")
            return
        ok = await send_trends_to_scenario(
            country=pending.get("country", DEFAULT_COUNTRY),
            trend_msg=pending.get("msg", ""),
            mode="trends_manual_send",
            source_kind="manual_button_send",
        )
        if ok:
            await query.message.reply_text("✅ تم إرسال القائمة إلى السيناريو.")
        else:
            await query.message.reply_text("❌ فشل إرسال القائمة إلى السيناريو.")

    elif data == "trend_mode_manual":
        if not is_owner:
            return
        context.user_data["trends_mode"] = "manual"
        try:
            await query.edit_message_reply_markup(reply_markup=get_trends_actions_keyboard("manual"))
        except Exception:
            pass
        await query.message.reply_text("✅ تم التحويل إلى الوضع اليدوي. عند فتح الترند سيظهر زر الإرسال.")

    elif data == "trend_mode_auto":
        if not is_owner:
            return
        context.user_data["trends_mode"] = "auto"
        try:
            await query.edit_message_reply_markup(reply_markup=get_trends_actions_keyboard("auto"))
        except Exception:
            pass
        await query.message.reply_text("✅ تم التحويل إلى الوضع التلقائي. عند فتح الترند سيُرسل مباشرة إلى السيناريو.")

    elif data == "check_join":
        if await is_channel_member(user_id, context):
            await query.edit_message_text(
                "شكراً لاشتراكك! يمكنك الآن استخدام البوت بالكامل.",
                reply_markup=get_main_menu_keyboard(is_owner=is_owner),
            )
        else:
            await query.answer("لم تقم بالاشتراك بعد!", show_alert=True)

# ---------------------------------------------------------
# Commands Definitions
# ---------------------------------------------------------
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("الرجاء كتابة اسم المنتج بعد الأمر. مثال: /search ساعة")
        return
    raw_keyword = " ".join(context.args)
    user_country = context.user_data.get("country", DEFAULT_COUNTRY)
    await perform_search_logic(update, context, raw_keyword, user_country)

async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("الاستخدام: /link https://...")
        return
    source_url = context.args[0].strip()
    aff = await generate_affiliate_link_async(
        source_url=source_url,
        ship_to_country=DEFAULT_COUNTRY,
    )
    if aff:
        await update.message.reply_text(f"رابط الأفلييت:\n{aff}")
    else:
        await update.message.reply_text("❌ لم أتمكن من تحويل الرابط.")

async def post_deals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("هذا الأمر للمدير فقط.")
        return
    await update.message.reply_text("⏳ جاري تجهيز وإرسال عرض...")
    ok = await post_one_deal_to_channel(context, country=DEFAULT_COUNTRY, platform="tiktok")
    await update.message.reply_text("✅ تم الإرسال للسيناريو." if ok else "❌ فشل النشر.")

async def trends_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    await handle_trends_request(update, context)

# ---------------------------------------------------------
# Text Message Handler
# ---------------------------------------------------------
async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id

    if not text or text.startswith("/"):
        return

    if context.user_data.get("awaiting_affiliate_post"):
        context.user_data["awaiting_affiliate_post"] = False
        if user_id != OWNER_ID:
            await update.message.reply_text("غير مصرح.")
            return
        url, _custom_title = extract_url_and_title(text)
        if not url or not is_url(url):
            await update.message.reply_text("رجاءً إرسال رابط صحيح يبدأ بـ http/https.")
            return
        aff = await generate_affiliate_link_async(
            source_url=url,
            ship_to_country=DEFAULT_COUNTRY,
        )
        if aff:
            await update.message.reply_text(f"✅ تم تحويل الرابط بنجاح:\n{aff}")
        else:
            await update.message.reply_text("❌ لم أتمكن من تحويل الرابط.")
        return

    if context.user_data.get("awaiting_manual_post"):
        context.user_data["awaiting_manual_post"] = False
        if user_id != OWNER_ID:
            await update.message.reply_text("غير مصرح.")
            return
        url, custom_title = extract_url_and_title(text)
        if not url or not is_url(url):
            await update.message.reply_text("رجاءً إرسال رابط صحيح.")
            return
        await admin_prepare_post_from_url(update, context, source_url=url, custom_title=custom_title)
        return

    user_country = context.user_data.get("country", DEFAULT_COUNTRY)
    await perform_search_logic(update, context, text, user_country)

# ---------------------------------------------------------
# Main Execution
# ---------------------------------------------------------
def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing!")

    keep_alive()

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("link", link_command))
    application.add_handler(CommandHandler("postdeals", post_deals_command))
    application.add_handler(CommandHandler("trends", trends_command))

    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    application.job_queue.run_daily(
        scheduled_trends,
        time=dtime(hour=8, minute=0),
        name="daily_trends_08_00",
    )
    application.job_queue.run_daily(
        scheduled_daily_deal,
        time=dtime(hour=12, minute=7),
        name="daily_deal_12_07",
    )

    print("🤖 Bot Started with Trends + Manual/Auto Scenario Send...")
    application.run_polling()

if __name__ == "__main__":
    main()
