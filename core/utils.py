import logging
import os
import textwrap
import sys
import re
#from io import BytesIO
from django.core.files.base import ContentFile
from django.utils import timezone
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont
import barcode
from barcode.writer import ImageWriter
from celery import group
from django.conf import settings
import time



logger = logging.getLogger('core')

def get_font_by_type(size, font_type="bold"):
    """Loads ArialBold or Roboto_Condensed-Bold dynamically."""
    fname = 'Roboto_Condensed-Bold.ttf' if font_type == "condensed" else 'ArialBold.ttf'
    font_path = os.path.join(settings.BASE_DIR, 'core', 'static', 'fonts', fname)
    try:
        if os.path.exists(font_path):
            return ImageFont.truetype(font_path, size)
        return ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()

def get_dynamic_font_size(text, max_w, max_h, initial_size, font_type="bold"):
    """Shrinks font until it fits the specified pixel box."""
    size = initial_size
    font = get_font_by_type(size, font_type)
    d = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    while size > 8:
        bbox = d.textbbox((0, 0), text, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if w <= max_w and h <= max_h:
            break
        size -= 2
        font = get_font_by_type(size, font_type)
    return font

def template_v1(image, draw, product, width, height, color_scheme):
    """
    ORIGINAL DESIGN (Template 1)
    Handles its own background logic, split-screen, and promo coloring.
    Features superscript cents and dynamic scaling.
    """
    is_promo = getattr(product, 'is_on_special', False)
    is_bw_only = 'R' not in color_scheme and 'Y' not in color_scheme
    
    # 1. Background Logic
    # Left side: Yellow for promo if available, else White
    left_bg = (255, 255, 0) if (is_promo and 'Y' in color_scheme) else (255, 255, 255)
    
    # Right side (Price Box): 
    # Red for promo tags, Black for BW-only promo tags, else White
    if 'R' in color_scheme or 'Y' in color_scheme:
        price_bg = (255, 0, 0) 
        price_txt_col = (255, 255, 255)        
    else:
        if is_promo and is_bw_only:
            price_bg = (0, 0, 0) 
            price_txt_col = (255, 255, 255)   
        else:
            price_bg = (255, 255, 255) 
            price_txt_col = (0, 0, 0)                 

    # if is_promo:
    #     if 'R' in color_scheme or 'Y' in color_scheme:
    #         price_bg = (255, 0, 0) 
    #         price_txt_col = (255, 255, 255)
    #     else:
    #         # BW Tag Special handling
    #         price_bg = (0, 0, 0)
    #         price_txt_col = (255, 255, 255)
    # else:
    #     price_bg = (255, 255, 255)
    #     price_txt_col = (0, 0, 0)

    # Fill Background
    draw.rectangle([0, 0, width, height], fill=left_bg)
    
    # 2. Split Logic
    split_x = int(width * 0.62)
    safe_pad = 3
    draw.rectangle([split_x, 0, width, height], fill=price_bg)

    if product:
        # 3. Price Calculation (Dollars and Cents)
        try:
            price_val = float(product.price)
            p_parts = f"{price_val:.2f}".split('.')
            dollars, cents = f"${p_parts[0]}", p_parts[1]
        except (ValueError, TypeError):
            dollars, cents = "$0", "00"

        # 4. Price Scaling Logic
        max_p_w = (width - split_x) - (safe_pad * 2)
        p_size = int(height * 0.50) if is_promo else int(height * 0.60)
        
        # Adjust size to fit box
        while p_size > 12:
            d_font = get_font_by_type(p_size, "bold")
            c_font = get_font_by_type(int(p_size * 0.45), "bold")
            d_bbox = draw.textbbox((0, 0), dollars, font=d_font)
            c_bbox = draw.textbbox((0, 0), cents, font=c_font)
            d_w = d_bbox[2] - d_bbox[0]
            c_w = c_bbox[2] - c_bbox[0]
            
            if (d_w + c_w + 4) <= max_p_w:
                break
            p_size -= 2

        # 5. Centering and Drawing Price
        total_p_w = d_w + c_w + 4
        p_x = split_x + ((width - split_x) - total_p_w) // 2
        y_center = height // 2
        y_offset = int(height * 0.12) if is_promo else 0 # Slightly lower to fit "SPECIAL" text

        # Draw "SPECIAL" text for BW tags if on promo
        if is_promo:
            promo_font = get_font_by_type(20, "bold")
            draw.text((split_x + (width - split_x)//2, 10), "SPECIAL", fill=price_txt_col, font=promo_font, anchor="mt")

        # Draw Dollars
        draw.text((p_x, y_center + y_offset), dollars, fill=price_txt_col, font=d_font, anchor="lm")

        # Draw Cents (Superscripted)
        cents_x = p_x + d_w + 2
        cents_y = (y_center + y_offset) - int(p_size * 0.18) 
        draw.text((cents_x, cents_y), cents, fill=price_txt_col, font=c_font, anchor="lm")

        # 6. SKU
        sku_font = get_font_by_type(10, "bold")
        draw.text((safe_pad, safe_pad), f"SKU: {product.sku}", fill=(0,0,0), font=sku_font)

        # 7. PRODUCT NAME (Multi-line Shrink)
        name_zone_y1 = 20
        name_zone_y2 = height - int(height * 0.30)
        
        wrapper = textwrap.TextWrapper(width=18)
        lines = wrapper.wrap(text=product.name)[:3]
        
        if lines:
            n_size = 14
            while n_size > 7:
                n_font = get_font_by_type(n_size, "condensed")
                max_line_w = max([draw.textbbox((0, 0), line, font=n_font)[2] for line in lines])
                total_n_h = len(lines) * (n_size + 2)
                
                if max_line_w <= (split_x - (safe_pad * 2)) and total_n_h <= (name_zone_y2 - name_zone_y1):
                    break
                n_size -= 1
            
            curr_y = name_zone_y1
            for line in lines:
                draw.text((safe_pad, curr_y), line, fill=(0,0,0), font=n_font)
                curr_y += n_size + 2

        # 8. Barcode
        try:
            code128 = barcode.get_barcode_class('code128')
            ean = code128(str(product.sku), writer=ImageWriter())
            b_img = ean.render(writer_options={"write_text": False, "quiet_zone": 1})
            b_img = b_img.resize((split_x - 10, int(height * 0.25)), Image.NEAREST).convert("RGBA")
            image.paste(b_img, (safe_pad, height - b_img.height - safe_pad), b_img)
        except Exception as e:
            logger.error(f"Barcode drawing error: {e}")
            
def template_v2(image, draw, product, width, height, color_scheme):
    """
    NEW DESIGN (Template 2)
    High visibility centered layout with bottom-left 'SPECIAL' indicator.
    """
    is_promo = getattr(product, 'is_on_special', False)
    color_scheme = color_scheme.upper()
    bg_color = (255, 255, 0) if (is_promo and 'Y' in color_scheme) else (255, 255, 255)
    draw.rectangle([0, 0, width, height], fill=bg_color)

    if not product: return

    # Header Bar
    draw.rectangle([0, 0, width, 25], fill=(0, 0, 0))
    name_font = get_font_by_type(14, "bold")
    draw.text((width//2, 12), product.name.upper()[:25], fill=(255,255,255), font=name_font, anchor="mm")

    # Large Center Price
    price_str = f"${product.price}"
    price_font = get_dynamic_font_size(price_str, width - 20, height - 60, 55)
    p_color = (255, 0, 0) if ('R' in color_scheme) else (0,0,0)
    draw.text((width//2, height//2 + 5), price_str, fill=p_color, font=price_font, anchor="mm")

    # Bottom Right SKU
    sku_font = get_font_by_type(10, "condensed")
    draw.text((width - 5, height - 5), f"SKU: {product.sku}", fill=(0,0,0), font=sku_font, anchor="rb")

    # --- NEW: SPECIAL TAG FOR V2 ---
    if is_promo:
        promo_tag_font = get_font_by_type(20, "bold")
        draw.text((5, height - 5), "SPECIAL", fill=(0,0,0), font=promo_tag_font, anchor="lb")

def template_v3(image, draw, product, width, height, color_scheme):
    """
    SIDE-BY-SIDE PROMO (Template 3)
    Inspired by 'Organic Blueberries' design.
    Left: Product Info (White). Right: Special Offer (Yellow/Red).
    """
    is_promo = getattr(product, 'is_on_special', False)
    color_scheme = color_scheme.upper()
    
    # 1. Background Split
    split_x = int(width * 0.45)
    draw.rectangle([0, 0, split_x, height], fill=(255, 255, 255)) # Left Info
    
    # Right side is Yellow if on promo, else White
    right_bg = (255, 255, 0) if (is_promo and 'Y' in color_scheme) else (255, 255, 255)
    draw.rectangle([split_x, 0, width, height], fill=right_bg)

    if not product: return

    # 2. Left Side: Product Details
    safe_pad = 8
    # SKU at top
    sku_font = get_font_by_type(10, "bold")
    draw.text((safe_pad, safe_pad), f"SKU: {product.sku}", fill=(0,0,0), font=sku_font)

    # Product Name (Wrapped)
    name_font = get_font_by_type(16, "bold")
    wrapper = textwrap.TextWrapper(width=12)
    lines = wrapper.wrap(text=product.name.upper())[:4]
    curr_y = safe_pad + 18
    for line in lines:
        draw.text((safe_pad, curr_y), line, fill=(0,0,0), font=name_font)
        curr_y += 18

    # Bottom Border Line on Left
    draw.line([safe_pad, height - 35, split_x - safe_pad, height - 35], fill=(0,0,0), width=1)

    # 3. Right Side: Pricing
    if is_promo:
        # "SALE!" or "SPECIAL OFFER" Banner
        banner_font = get_font_by_type(18, "bold")
        banner_color = (255, 0, 0) if 'R' in color_scheme else (0,0,0)
        draw.text((split_x + (width - split_x)//2, 25), "SALE!", fill=banner_color, font=banner_font, anchor="mm")
        
        # Sub-header
        sub_font = get_font_by_type(10, "bold")
        draw.rectangle([split_x + 10, 40, width - 10, 55], fill=banner_color)
        draw.text((split_x + (width - split_x)//2, 47), "SPECIAL OFFER", fill=(255,255,255), font=sub_font, anchor="mm")
    
    # Main Price
    price_str = f"${product.price}"
    max_p_w = (width - split_x) - 10
    price_font = get_dynamic_font_size(price_str, max_p_w, height // 2, 45)
    draw.text((split_x + (width - split_x)//2, height - 40), price_str, fill=(0,0,0), font=price_font, anchor="mm")


def generate_esl_image(tag_id):
    """
    Main controller for image generation.
    """
    from .models import ESLTag
    try:
        tag = ESLTag.objects.select_related('hardware_spec', 'paired_product').get(pk=tag_id)
        spec = tag.hardware_spec
        product = tag.paired_product
        
        width = int(spec.width_px or 296)
        height = int(spec.height_px or 128)
        color_scheme = (spec.color_scheme or "BW").upper()

        image = Image.new('RGB', (width, height), color=(255, 255, 255))
        draw = ImageDraw.Draw(image)

        tid = getattr(tag, 'template_id', 1)
        if tid == 3:
            template_v3(image, draw, product, width, height, color_scheme)
        elif tid == 2:
            template_v2(image, draw, product, width, height, color_scheme)
        else:
            template_v1(image, draw, product, width, height, color_scheme)

        return image
    except Exception as e:
        logger.error(f"Critical error in generate_esl_image: {e}", exc_info=True)
        return Image.new('RGB', (296, 128), color=(255, 255, 255))

def trigger_bulk_sync(tag_ids):
    """
    Uses Celery groups to process multiple tags efficiently.
    """
    from core.tasks import update_tag_image_task
    from .models import ESLTag

    valid_tag_ids = list(ESLTag.objects.filter(
        id__in=tag_ids,
        paired_product__isnull=False,
        hardware_spec__isnull=False
    ).values_list('id', flat=True))

    if not valid_tag_ids:
        return None

    job_group = group(update_tag_image_task.s(tid) for tid in valid_tag_ids)
    result = job_group.apply_async()
    result.save() 
    return result