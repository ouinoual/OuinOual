# video_generator.py
import os
import requests
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import ImageClip
import tempfile

def download_image(url, output_path):
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            with open(output_path, "wb") as f:
                f.write(resp.content)
            return True
    except:
        pass
    return False

def create_video_from_deal(deal, output_path, duration=5.0):
    with tempfile.TemporaryDirectory() as tmpdir:
        image_url = deal.get("image_url") or deal.get("product_main_image_url")
        if not image_url:
            return None
            
        image_path = os.path.join(tmpdir, "product.jpg")
        if not download_image(image_url, image_path):
            return None
        
        # إنشاء صورة مصممة
        width, height = 1080, 1920
        img = Image.open(image_path)
        img.thumbnail((800, 800))
        
        background = Image.new('RGB', (width, height), '#0f0f23')
        draw = ImageDraw.Draw(background)
        background.paste(img, ((width-800)//2, 150))
        
        # نصوص
        title = deal.get("title", "")[:40]
        price = str(deal.get("new_price", ""))
        discount = str(deal.get("discount_pct", ""))
        
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 50)
        except:
            font = ImageFont.load_default()
        
        draw.text((width//2, 1100), title, fill="white", font=font, anchor="mm")
        draw.text((width//2, 1250), price, fill="#00ff88", font=font, anchor="mm")
        if discount:
            draw.text((width//2, 1400), f"خصم {discount}", fill="#ff4757", font=font, anchor="mm")
        draw.text((width//2, 1650), "رابط في البايو", fill="#ffd700", font=font, anchor="mm")
        
        slide_path = os.path.join(tmpdir, "slide.jpg")
        background.save(slide_path)
        
        # تحويل إلى فيديو
        clip = ImageClip(slide_path, duration=duration)
        clip.write_videofile(output_path, fps=24, codec='libx264', audio=False, verbose=False, logger=None)
        
        return output_path
