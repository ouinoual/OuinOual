import os
import tempfile
from pathlib import Path

import arabic_reshaper
import requests
from bidi.algorithm import get_display
from moviepy.editor import ImageClip
from PIL import Image, ImageDraw, ImageFont, ImageOps

BASE_DIR = Path(__file__).resolve().parent
FONTS_DIR = BASE_DIR / "assets" / "fonts"
ARABIC_FONT_CANDIDATES = [
    FONTS_DIR / "Amiri-Bold.ttf",
    FONTS_DIR / "Cairo-Bold.ttf",
    FONTS_DIR / "Cairo-Regular.ttf",
    FONTS_DIR / "Amiri-Regular.ttf",
]


def download_image(url, output_path):
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            with open(output_path, "wb") as f:
                f.write(resp.content)
            return True
    except Exception:
        pass
    return False


def reshape_arabic_text(text: str) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    if not any("\u0600" <= ch <= "\u06FF" for ch in text):
        return text
    return get_display(arabic_reshaper.reshape(text))


def load_font(size: int):
    for font_path in ARABIC_FONT_CANDIDATES:
        if font_path.exists():
            return ImageFont.truetype(str(font_path), size)
    return ImageFont.load_default()


def draw_centered_text(draw, text, y, font, fill, width):
    text = reshape_arabic_text(text)
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (width - (bbox[2] - bbox[0])) // 2
    draw.text((x, y), text, fill=fill, font=font)


def create_video_from_deal(deal, output_path, duration=5.0):
    with tempfile.TemporaryDirectory() as tmpdir:
        image_url = deal.get("image_url") or deal.get("product_main_image_url") or deal.get("productMainImageUrl")
        if not image_url:
            return None

        image_path = os.path.join(tmpdir, "product.jpg")
        if not download_image(image_url, image_path):
            return None

        width, height = 1080, 1920
        product_img = Image.open(image_path).convert("RGB")
        product_img.thumbnail((860, 860))
        product_img = ImageOps.contain(product_img, (860, 860))

        background = Image.new("RGB", (width, height), "#0f0f23")
        draw = ImageDraw.Draw(background)

        draw.rounded_rectangle(
            (80, 80, width - 80, height - 80),
            radius=42,
            fill="#171735",
            outline="#2b2b59",
            width=3
        )

        background.paste(product_img, ((width - product_img.width) // 2, 170))

        title = str(deal.get("title") or deal.get("product_title") or "")[:70]
        price = str(deal.get("new_price") or deal.get("sale_price") or "")
        discount = str(deal.get("discount_pct") or deal.get("discount") or "")

        draw_centered_text(draw, title, 1110, load_font(52), "white", width)
        if price:
            draw_centered_text(draw, price, 1280, load_font(60), "#00ff88", width)
        if discount and discount not in {"0", "0.0", "None"}:
            draw_centered_text(draw, f"خصم {discount}", 1435, load_font(48), "#ff6b6b", width)
        draw_centered_text(draw, "رابط في البايو", 1640, load_font(46), "#ffd166", width)

        slide_path = os.path.join(tmpdir, "slide.jpg")
        background.save(slide_path, quality=95)

        clip = ImageClip(slide_path, duration=duration)
        clip.write_videofile(output_path, fps=24, codec="libx264", audio=False, verbose=False, logger=None)
        return output_path
