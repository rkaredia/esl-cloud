import logging
import os
import textwrap
import sys
from io import BytesIO
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
    from .models import ESLTag
    start_time = time.time() # Start the clock


    try:
        tag = ESLTag.objects.select_related('hardware_spec', 'paired_product').get(pk=tag_id)
        tag.refresh_from_db()
        spec = tag.hardware_spec
        product = tag.paired_product
        
        width = int(spec.width_px or 296)
        height = int(spec.height_px or 128)
        color_scheme = (spec.color_scheme or "BW").upper()
        is_promo = getattr(product, 'is_on_special', False)


        model_name = spec.model_number if spec else "N/A"
        product_name = tag.paired_product.name if tag.paired_product else "Unpaired"

        # The Fixed Log Line
        logger.info(
            f"***IMAGE_GEN*** | Start | MAC: {tag.tag_mac} | Res: {width}x{height} | "
            f"Color scheme: {color_scheme} |Model: {model_name} | Product: {product_name} |Promo: {is_promo}"
        )

        # 1. DYNAMIC COLOR SCHEME
        left_bg = 'yellow' if (is_promo and 'Y' in color_scheme) else 'white'
        logger.info(f"*IMAGE_GEN* | left_bg: {left_bg} ")
        if ('R' in color_scheme or 'Y' in color_scheme):
            # Red if BWR/BWRY, otherwise Black for BW
            price_bg = 'red' 
            price_txt_col = 'white'
        else:
            price_bg = 'white'
            price_txt_col = 'black'

        image = Image.new('RGB', (width, height), color=left_bg)
        draw = ImageDraw.Draw(image)

        # 2. DEFINE ZONES & COORDINATES
        split_x = int(width * 0.62)
        safe_pad = 3
        
        # Fixed heights for zones
        sku_h = int(height * 0.12)
        barcode_h = int(height * 0.25)
        name_zone_y1 = sku_h + safe_pad
        name_zone_y2 = height - barcode_h - safe_pad

        # Draw Price Box Zone
        draw.rectangle([split_x, 0, width, height], fill=price_bg)

        # 3. ADD "SALE" LABEL
        if is_promo:
            label_text = "SALE" if 'Y' not in color_scheme else "SPECIAL"
            label_font = get_font_by_type(int(height * 0.12), "bold")
            l_bbox = draw.textbbox((0, 0), label_text, font=label_font)
            l_w = l_bbox[2] - l_bbox[0]
            draw.text((split_x + (width - split_x - l_w)//2, safe_pad), label_text, fill=price_txt_col, font=label_font)

        # 4. PRICE CALCULATION (Shrink-to-Fit)
        price_val = float(product.price)
        p_parts = f"{price_val:.2f}".split('.')
        dollars, cents = f"${p_parts[0]}", p_parts[1]

        max_p_w = (width - split_x) - (safe_pad * 2)
        p_size = int(height * 0.55) if is_promo else int(height * 0.60)
        
        while p_size > 12:
            d_font = get_font_by_type(p_size, "bold")
            c_font = get_font_by_type(int(p_size * 0.45), "bold")
            d_w = draw.textbbox((0, 0), dollars, font=d_font)[2]
            c_w = draw.textbbox((0, 0), cents, font=c_font)[2]
            if (d_w + c_w + 4) <= max_p_w:
                break
            p_size -= 2

        total_p_w = d_w + c_w + 4
        p_x = split_x + ((width - split_x) - total_p_w) // 2
        y_offset = int(height * 0.1) if is_promo else 0
        draw.text((p_x, (height // 2) + y_offset), dollars, fill=price_txt_col, font=d_font, anchor="lm")
        draw.text((p_x + d_w + 2, (height // 2) + y_offset - int(p_size * 0.15)), cents, fill=price_txt_col, font=c_font, anchor="lm")
        
        # 5. DRAW SKU
        sku_font = get_font_by_type(max(10, int(height * 0.10)), "bold")
        draw.text((safe_pad, safe_pad), f"SKU: {product.sku}", fill='black', font=sku_font)

        # 6. PRODUCT NAME (Multi-line Shrink)
        wrapper = textwrap.TextWrapper(width=18)
        lines = wrapper.wrap(text=product.name)[:3]
        
        if lines:
            n_size = int(height * 0.20)
            while n_size > 7:
                n_font = get_font_by_type(n_size, "condensed")
                max_line_w = max([draw.textbbox((0, 0), line, font=n_font)[2] for line in lines])
                total_n_h = len(lines) * (n_size + 1)
                if max_line_w <= (split_x - safe_pad) and total_n_h <= (name_zone_y2 - name_zone_y1):
                    break
                n_size -= 1
            
            curr_y = name_zone_y1
            for line in lines:
                draw.text((safe_pad, curr_y), line, fill='black', font=n_font)
                curr_y += n_size + 1



        # 7. BARCODE
        if product.sku:
            code128 = barcode.get_barcode_class('code128')
            ean = code128(str(product.sku), writer=ImageWriter())
            b_img = ean.render(writer_options={"write_text": False, "quiet_zone": 1})
            b_img = b_img.resize((split_x - (safe_pad * 2), barcode_h), Image.NEAREST).convert("RGBA")
            image.paste(b_img, (safe_pad, height - barcode_h - safe_pad), b_img)

        # Logging the start of the process
        logger.info(f"Starting image generation for Tag: {tag.tag_mac} ({width}x{height})")

        # 8. SAVE
        temp = BytesIO()
        image.save(temp, format='PNG')
        tag.tag_image.save(f"{tag.tag_mac}.png", ContentFile(temp.getvalue()), save=False)
        tag.save()
        duration = time.time() - start_time
        logger.info(f"***IMAGE_GEN*** | SUCCESS: {tag.tag_mac} generated in {duration:.3f}s")
        return f"MAC: {tag.tag_mac} generated in {duration:.3f}s"
     

    except ESLTag.DoesNotExist:
        logger.error(f"Tag ID {tag_id} not found")
        return False
    except Exception as e:
        logger.error(f"CRITICAL ERROR for Tag {tag_id}: {e}", exc_info=True)
        raise e # Re-raise so Celery catches the failure properly  


# utils.py




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