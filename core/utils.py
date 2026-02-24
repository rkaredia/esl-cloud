import logging
import os
import textwrap
import sys
#from io import BytesIO
from django.core.files.base import ContentFile
from django.utils import timezone
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont
import barcode
from barcode.writer import ImageWriter
from celery import group
import time



logger = logging.getLogger('core')

def get_font_by_type(size, font_type="bold"):
    """Loads ArialBold or Roboto_Condensed-Bold dynamically."""
    # Using your specific filename: Roboto_Condensed-Bold.ttf
    fname = 'Roboto_Condensed-Bold.ttf' if font_type == "condensed" else 'ArialBold.ttf'
    font_path = os.path.join(settings.BASE_DIR, 'core', 'static', 'fonts', fname)
    try:
        return ImageFont.truetype(font_path, size)
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

def generate_esl_image(tag_id):
    """
    Generates the visual layout for the ESL tag.
    Returns: PIL Image object in RGB mode.
    """
    from .models import ESLTag
    logger.info(f"[UTILS] Drawing pixels for Tag ID: {tag_id}")
    try:
        tag = ESLTag.objects.select_related('hardware_spec', 'paired_product').get(pk=tag_id)
        
        spec = tag.hardware_spec
        product = tag.paired_product
        
        width = int(spec.width_px or 296)
        height = int(spec.height_px or 128)
        color_scheme = (spec.color_scheme or "BW").upper()
        is_promo = getattr(product, 'is_on_special', False)

        # 1. Background Logic (Using RGB tuples for 24-bit safety)
        left_bg = (255, 255, 0) if (is_promo and 'Y' in color_scheme) else (255, 255, 255)
        
        if ('R' in color_scheme or 'Y' in color_scheme):
            price_bg = (255, 0, 0) 
            price_txt_col = (255, 255, 255)
        else:
            price_bg = (255, 255, 255)
            price_txt_col = (0, 0, 0)

        image = Image.new('RGB', (width, height), color=left_bg)
        draw = ImageDraw.Draw(image)

        split_x = int(width * 0.62)
        safe_pad = 3
        
        # Draw Price Box Zone
        draw.rectangle([split_x, 0, width, height], fill=price_bg)

        if product:
            # Price Calculation
            price_val = float(product.price)
            p_parts = f"{price_val:.2f}".split('.')
            dollars, cents = f"${p_parts[0]}", p_parts[1]
            
            p_font = get_font_by_type(int(height * 0.5), "bold")
            draw.text((split_x + 5, height // 2), dollars, fill=price_txt_col, font=p_font, anchor="lm")
            
            # SKU
            sku_font = get_font_by_type(10, "bold")
            draw.text((safe_pad, safe_pad), f"SKU: {product.sku}", fill=(0,0,0), font=sku_font)

            # Product Name
            n_font = get_font_by_type(14, "condensed")
            wrapper = textwrap.TextWrapper(width=18)
            lines = wrapper.wrap(text=product.name)[:2]
            y = 20
            for line in lines:
                draw.text((safe_pad, y), line, fill=(0,0,0), font=n_font)
                y += 16

            # Barcode
            try:
                code128 = barcode.get_barcode_class('code128')
                ean = code128(str(product.sku), writer=ImageWriter())
                b_img = ean.render(writer_options={"write_text": False, "quiet_zone": 1})
                # Resize barcode to fit the layout
                b_img = b_img.resize((split_x - 10, int(height * 0.25)), Image.NEAREST).convert("RGBA")
                image.paste(b_img, (safe_pad, height - b_img.height - safe_pad), b_img)
            except Exception as e:
                logger.error(f"Barcode drawing error: {e}")
        logger.info(f"[UTILS] Successfully created {width}x{height} image object.")
        return image

    except Exception as e:
        logger.error(f"Critical error in generate_esl_image: {e}")
        # Return blank white image as fallback
        return Image.new('RGB', (296, 128), color=(255, 255, 255))



def trigger_bulk_sync(tag_ids):
    from core.tasks import update_tag_image_task
    from .models import ESLTag

    # Efficiency check: filter only valid tags
    valid_tag_ids = list(ESLTag.objects.filter(
        id__in=tag_ids,
        paired_product__isnull=False,
        hardware_spec__isnull=False
    ).values_list('id', flat=True))

    if not valid_tag_ids:
        return None

    job_group = group(update_tag_image_task.s(tid) for tid in valid_tag_ids)
    result = job_group.apply_async()
    
    # result.save() persists the list of task IDs so the Admin can see them
    result.save() 
    return result