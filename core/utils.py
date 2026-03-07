import logging
import os
import textwrap
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont
import barcode
from barcode.writer import ImageWriter
from celery import group

logger = logging.getLogger('core')

# Module-level cache to prevent redundant font loading from disk
_FONT_CACHE = {}
# Reusable Draw object for measurement to avoid creating new Image/Draw instances in loops
_MEASURE_DRAW = ImageDraw.Draw(Image.new('RGB', (1, 1)))

def get_font_by_type(size, font_type="bold"):
    """Loads ArialBold or Roboto_Condensed-Bold dynamically based on the requested style."""
    fname = 'Roboto_Condensed-Bold.ttf' if font_type == "condensed" else 'ArialBold.ttf'
    font_path = os.path.join(settings.BASE_DIR, 'core', 'static', 'fonts', fname)

    cache_key = (font_path, size)
    if cache_key in _FONT_CACHE:
        return _FONT_CACHE[cache_key]

    try:
        if os.path.exists(font_path):
            font = ImageFont.truetype(font_path, size)
            _FONT_CACHE[cache_key] = font
            return font
        return ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()

def get_dynamic_font_size(text, max_w, max_h, initial_size, font_type="bold"):
    """
    Uses binary search to find the largest font size that fits within the
    specified maximum width and height.
    """
    low = 8
    high = initial_size
    best_size = 8

    while low <= high:
        mid = (low + high) // 2
        font = get_font_by_type(mid, font_type)
        bbox = _MEASURE_DRAW.textbbox((0, 0), text, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]

        if w <= max_w and h <= max_h:
            best_size = mid
            low = mid + 1
        else:
            high = mid - 1

    return get_font_by_type(best_size, font_type)

def template_v1(image, draw, product, width, height, color_scheme):
    """
    Standard Split Design (Template 1)
    - Left side: Product name and barcode.
    - Right side: Large price display with superscript cents.
    """
    is_promo = getattr(product, 'is_on_special', False)
    is_bw_only = 'R' not in color_scheme and 'Y' not in color_scheme

    # 1. Colors & Background
    left_bg = (255, 255, 0) if (is_promo and 'Y' in color_scheme) else (255, 255, 255)
    if 'R' in color_scheme or 'Y' in color_scheme:
        price_bg = (255, 0, 0)
        price_txt_col = (255, 255, 255)
    else:
        price_bg = (0, 0, 0) if (is_promo and is_bw_only) else (255, 255, 255)
        price_txt_col = (255, 255, 255) if (is_promo and is_bw_only) else (0, 0, 0)

    draw.rectangle([0, 0, width, height], fill=left_bg)

    # 2. Dimensions
    split_x = int(width * 0.62)
    safe_pad = 4
    left_zone_w = split_x - (safe_pad * 2)
    draw.rectangle([split_x, 0, width, height], fill=price_bg)

    if not product: return

    # Product Name
    name_text = product.name.upper()
    wrapper = textwrap.TextWrapper(width=16)
    lines = wrapper.wrap(text=name_text)[:3]

    if lines:
        longest_line = max(lines, key=len)
        max_h_per_line = (height * 0.40) / len(lines)
        n_font = get_dynamic_font_size(longest_line, left_zone_w, max_h_per_line, 18, "condensed")

        # Get actual height for spacing
        bbox = draw.textbbox((0, 0), "Ay", font=n_font)
        line_height = bbox[3] - bbox[1] + 2

        curr_y = 4
        for line in lines:
            draw.text((safe_pad, curr_y), line, fill=(0,0,0), font=n_font)
            curr_y += line_height

    # Price rendering logic
    try:
        price_val = float(product.price)
        p_parts = f"{price_val:.2f}".split('.')
        dollars, cents = f"${p_parts[0]}", p_parts[1]
    except:
        dollars, cents = "$0", "00"

    # Calculate the available width inside the right-hand price box
    p_box_w = (width - split_x) - (safe_pad * 1)

    # Calculate a font size for the dollars that fits the box width and height.
    # We include "00" in the measurement string to reserve space for the superscript cents.
    d_font = get_dynamic_font_size(dollars + "0", p_box_w, height * 0.75, int(height * 0.70), "bold")

    # Calculate the exact pixel width of the dollar string at this font size
    d_bbox = draw.textbbox((0,0), dollars, font=d_font)
    d_w = d_bbox[2] - d_bbox[0]

    # Set the cents font to be roughly 45% the size of the dollar font for the superscript effect
    c_size = int(d_font.size * 0.45)
    c_font = get_font_by_type(c_size, "bold")
    c_w = draw.textbbox((0,0), cents, font=c_font)[2]
    total_p_w = d_w + c_w + 2

    # Calculate the starting X coordinate so the entire price is centered in the price box
    p_x = split_x + ((width - split_x) - total_p_w) // 2

    # Vertical alignment logic
    y_center = height // 2
    y_offset = int(height * 0.1) if is_promo else 0

    if is_promo:
        promo_font = get_dynamic_font_size("SPECIAL", p_box_w, 20, 20, "bold")
        draw.text((split_x + (width - split_x)//2, 8), "SPECIAL", fill=price_txt_col, font=promo_font, anchor="mt")

    draw.text((p_x, y_center + y_offset), dollars, fill=price_txt_col, font=d_font, anchor="lm")

    # Draw the cents slightly higher than the center line (superscript) using the offset from the dollar font size
    draw.text((p_x + d_w + 2, (y_center + y_offset) - int(d_font.size * 0.15)), cents, fill=price_txt_col, font=c_font, anchor="lm")

    # Barcode & SKU
    try:
        barcode_w, barcode_h = int(left_zone_w * 0.95), int(height * 0.25)
        raw_sku_data = str(product.sku)
        code128 = barcode.get_barcode_class('code128')
        ean = code128(raw_sku_data, writer=ImageWriter())

        # Render barcode bars without internal text to avoid resize distortion
        b_img = ean.render(writer_options={"write_text": False, "quiet_zone": 1})
        b_img = b_img.resize((barcode_w, barcode_h), Image.NEAREST).convert("RGBA")

        # Calculate Y for barcode: leave room for text below
        # We place the barcode slightly higher than the very bottom
        barcode_y = height - barcode_h - 15
        image.paste(b_img, (safe_pad, barcode_y), b_img)
        # Construct the "Human Readable" string for the Store Manager
        # Construct "Supplier:SKU" string
        supp_abbr = product.preferred_supplier.abbreviation if product.preferred_supplier else "SKU"
        display_text = f"{supp_abbr}:{product.sku}"

        s_font = get_dynamic_font_size(display_text, left_zone_w, 16, 14,  "condensed")
        # Draw text centered under barcode
        # anchor="mb" uses the middle of the text width and the bottom of the height
        draw.text((safe_pad + (barcode_w // 2), height - 2), display_text, fill=(0,0,0), font=s_font, anchor="mb")
    except Exception as e:
        logger.error(f"Barcode error: {e}")

def template_v2(image, draw, product, width, height, color_scheme):
    """Promo Design (Template 2) - Large centered price."""
    is_promo = getattr(product, 'is_on_special', False)
    bg_color = (255, 255, 0) if (is_promo and 'Y' in color_scheme) else (255, 255, 255)
    draw.rectangle([0, 0, width, height], fill=bg_color)
    if not product: return
    draw.rectangle([0, 0, width, 25], fill=(0, 0, 0))
    name_font = get_font_by_type(14, "bold")
    draw.text((width//2, 12), product.name.upper()[:25], fill=(255,255,255), font=name_font, anchor="mm")
    price_str = f"${product.price}"
    price_font = get_dynamic_font_size(price_str, width - 20, height - 60, 55)
    p_color = (255, 0, 0) if ('R' in color_scheme) else (0,0,0)
    draw.text((width//2, height//2 + 5), price_str, fill=p_color, font=price_font, anchor="mm")
    supp_abbr = product.preferred_supplier.abbreviation if product.preferred_supplier else "SKU"
    draw.text((width - 5, height - 5), f"{supp_abbr}: {product.sku}", fill=(0,0,0), font=get_font_by_type(12, "condensed"), anchor="rb")
    if is_promo: draw.text((5, height - 5), "SPECIAL", fill=(0,0,0), font=get_font_by_type(20, "bold"), anchor="lb")

def template_v3(image, draw, product, width, height, color_scheme):
    """Modern Design (Template 3)."""
    is_promo = getattr(product, 'is_on_special', False)
    split_x = int(width * 0.45)
    draw.rectangle([0, 0, split_x, height], fill=(255, 255, 255))
    right_bg = (255, 255, 0) if (is_promo and 'Y' in color_scheme) else (255, 255, 255)
    draw.rectangle([split_x, 0, width, height], fill=right_bg)
    if not product: return
    safe_pad = 8
    supp_abbr = product.preferred_supplier.abbreviation if product.preferred_supplier else "SKU"
    draw.text((safe_pad, safe_pad), f"{supp_abbr}: {product.sku}", fill=(0,0,0), font=get_font_by_type(10, "bold"))
    name_font = get_font_by_type(16, "bold")
    lines = textwrap.wrap(text=product.name.upper(), width=12)[:4]
    curr_y = safe_pad + 18
    for line in lines:
        draw.text((safe_pad, curr_y), line, fill=(0,0,0), font=name_font)
        curr_y += 18
    if is_promo:
        banner_color = (255, 0, 0) if 'R' in color_scheme else (0,0,0)
        draw.text((split_x + (width - split_x)//2, 25), "SALE!", fill=banner_color, font=get_font_by_type(18, "bold"), anchor="mm")
    price_str = f"${product.price}"
    price_font = get_dynamic_font_size(price_str, (width - split_x) - 10, height // 2, 45)
    draw.text((split_x + (width - split_x)//2, height - 40), price_str, fill=(0,0,0), font=price_font, anchor="mm")

def generate_esl_image(tag_id, tag_instance=None):
        # Bolt: Support instance passing to avoid redundant SELECT query when called from a task.
    """Core logic to generate a BMP image for an ESL tag based on its template."""
    from .models import ESLTag
    try:
        if tag_instance:
            tag = tag_instance
        else:
            tag = ESLTag.objects.select_related('hardware_spec', 'paired_product__preferred_supplier').get(pk=tag_id)

        spec, product = tag.hardware_spec, tag.paired_product
        width, height = int(spec.width_px or 296), int(spec.height_px or 128)
        color_scheme = (spec.color_scheme or "BW").upper()
        image = Image.new('RGB', (width, height), color=(255, 255, 255))
        draw = ImageDraw.Draw(image)
        tid = getattr(tag, 'template_id', 1)
        if tid == 3: template_v3(image, draw, product, width, height, color_scheme)
        elif tid == 2: template_v2(image, draw, product, width, height, color_scheme)
        else: template_v1(image, draw, product, width, height, color_scheme)
        return image
    except Exception as e:
        logger.error(f"Critical error in generate_esl_image: {e}", exc_info=True)
        return Image.new('RGB', (296, 128), color=(255, 255, 255))

def trigger_bulk_sync(tag_ids):
    """Triggers background updates for a list of tags using Celery groups."""
    from core.tasks import update_tag_image_task
    from .models import ESLTag
    valid_tag_ids = list(ESLTag.objects.filter(id__in=tag_ids, paired_product__isnull=False, hardware_spec__isnull=False).values_list('id', flat=True))
    if not valid_tag_ids: return None
    job_group = group(update_tag_image_task.s(tid) for tid in valid_tag_ids)
    result = job_group.apply_async()
    result.save()
    return result
