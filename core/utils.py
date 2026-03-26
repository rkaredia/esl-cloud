import logging
import os
import textwrap
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont
import barcode
from barcode.writer import ImageWriter
from celery import group

"""
SAIS UTILITIES: IMAGE RENDERING & HELPER FUNCTIONS
--------------------------------------------------
This module handles the "Physical Layer" of the digital labels.
It uses the 'Pillow' (PIL) library to draw text, lines, and barcodes
onto a canvas, which is then saved as a BMP file for the ESL tags.

Key Components:
1. FONT MANAGEMENT: Dynamic resizing to ensure text always fits the screen.
2. TEMPLATES: Different visual layouts (Standard, Promo, Modern).
3. BARCODE GENERATION: Creating Code128 barcodes from SKUs.
4. TASK TRIGGERING: Helpers for mass-updating tags.
"""

logger = logging.getLogger('core')

# Module-level cache to prevent redundant font loading from disk (Performance)
_FONT_CACHE = {}

# Reusable Draw object for measurement to avoid creating new Image instances (Performance)
_MEASURE_DRAW = ImageDraw.Draw(Image.new('RGB', (1, 1)))

def get_font_by_type(size, font_type="bold"):
    """
    FONT LOADER
    -----------
    Loads a TrueType font (.ttf) from the static folder.
    - 'bold': Arial Bold (Classic look)
    - 'condensed': Roboto Condensed (Fits more text)
    """
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
    AUTO-FIT LOGIC
    --------------
    Uses 'Binary Search' to find the largest font size that fits within
    the given width (max_w) and height (max_h).
    Ensures long product names don't spill off the edge of the tag.
    """
    low = 8
    high = initial_size
    best_size = 8

    while low <= high:
        mid = (low + high) // 2
        font = get_font_by_type(mid, font_type)
        # textbbox calculates the pixel dimensions of the text
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
    LAYOUT: STANDARD SPLIT (V1)
    ---------------------------
    - Left 62%: Product Name (top) and Barcode (bottom).
    - Right 38%: Large Price with superscript cents.
    - Background: Yellow if on special and supported by hardware.
    """
    is_promo = getattr(product, 'is_on_special', False)
    is_bw_only = 'R' not in color_scheme and 'Y' not in color_scheme

    # 1. Colors & Background logic
    left_bg = (255, 255, 0) if (is_promo and 'Y' in color_scheme) else (255, 255, 255)
    if 'R' in color_scheme or 'Y' in color_scheme:
        price_bg = (255, 0, 0) # Red/Yellow tags use a bright price box
        price_txt_col = (255, 255, 255)
    else:
        price_bg = (0, 0, 0) if (is_promo and is_bw_only) else (255, 255, 255)
        price_txt_col = (255, 255, 255) if (is_promo and is_bw_only) else (0, 0, 0)

    draw.rectangle([0, 0, width, height], fill=left_bg)

    # 2. Split Screen
    split_x = int(width * 0.62)
    safe_pad = 4
    left_zone_w = split_x - (safe_pad * 2)
    draw.rectangle([split_x, 0, width, height], fill=price_bg)

    if not product: return

    # 3. DRAW PRODUCT NAME (Left Top)
    name_text = product.name.upper()
    wrapper = textwrap.TextWrapper(width=16) # Wrap text to prevent overflow
    lines = wrapper.wrap(text=name_text)[:4] # Max 4 lines

    if lines:
        # Start with a larger font size and let it scale down to fit the zone
        initial_font_size = 30
        longest_line = max(lines, key=len)
        max_h_total = height * 0.45
        max_h_per_line = max_h_total / len(lines)

        n_font = get_dynamic_font_size(longest_line, left_zone_w, max_h_per_line, initial_font_size, "condensed")

        bbox = draw.textbbox((0, 0), "Ay", font=n_font)
        line_height = bbox[3] - bbox[1] + 2

        curr_y = 4
        for line in lines:
            draw.text((safe_pad, curr_y), line, fill=(0,0,0), font=n_font)
            curr_y += line_height

    # 4. DRAW PRICE & SUPPLIER (Right Box)
    try:
        price_val = float(product.price)
        p_parts = f"{price_val:.2f}".split('.')
        dollars, cents = f"${p_parts[0]}", p_parts[1]
    except:
        dollars, cents = "$0", "00"

    p_box_w = (width - split_x) - (safe_pad * 1)

    # Calculate available height for price
    price_h_limit = height * 0.60
    d_font = get_dynamic_font_size(dollars + "0", p_box_w, price_h_limit, int(height * 0.65), "bold")

    d_bbox = draw.textbbox((0,0), dollars, font=d_font)
    d_w = d_bbox[2] - d_bbox[0]

    # SUPERSCRIPT: Cents are 45% of the size of dollars
    c_size = int(d_font.size * 0.45)
    c_font = get_font_by_type(c_size, "bold")
    c_w = draw.textbbox((0,0), cents, font=c_font)[2]
    total_p_w = d_w + c_w + 2

    p_x = split_x + ((width - split_x) - total_p_w) // 2
    y_center = height // 2

    # Supplier Abbreviation (Always visible at bottom)
    supp_abbr = product.preferred_supplier.abbreviation if product.preferred_supplier else ""
    supp_font = get_dynamic_font_size(supp_abbr, p_box_w, 20, 16, "bold")
    draw.text((split_x + (width - split_x)//2, height - 8), supp_abbr, fill=price_txt_col, font=supp_font, anchor="mb")

    if is_promo:
        promo_font = get_dynamic_font_size("SPECIAL", p_box_w, 20, 20, "bold")
        draw.text((split_x + (width - split_x)//2, 8), "SPECIAL", fill=price_txt_col, font=promo_font, anchor="mt")

    # Draw dollars and then draw cents slightly higher
    draw.text((p_x, y_center), dollars, fill=price_txt_col, font=d_font, anchor="lm")
    draw.text((p_x + d_w + 2, (y_center) - int(d_font.size * 0.15)), cents, fill=price_txt_col, font=c_font, anchor="lm")

    # 5. DRAW BARCODE (Left Bottom)
    try:
        barcode_w, barcode_h = int(left_zone_w * 0.95), int(height * 0.25)
        raw_sku_data = str(product.sku)
        code128 = barcode.get_barcode_class('code128')
        ean = code128(raw_sku_data, writer=ImageWriter())

        b_img = ean.render(writer_options={"write_text": False, "quiet_zone": 1})
        b_img = b_img.resize((barcode_w, barcode_h), Image.NEAREST).convert("RGBA")

        barcode_y = height - barcode_h - 15
        image.paste(b_img, (safe_pad, barcode_y), b_img)

        display_text = f"{product.sku}"

        s_font = get_dynamic_font_size(display_text, left_zone_w, 16, 14,  "condensed")
        draw.text((safe_pad + (barcode_w // 2), height - 2), display_text, fill=(0,0,0), font=s_font, anchor="mb")
    except Exception as e:
        logger.error(f"Barcode error: {e}")

def template_v2(image, draw, product, width, height, color_scheme):
    """
    LAYOUT: PROMO / LARGE PRICE (V2)
    --------------------------------
    Designed for maximum visibility from a distance.
    """
    is_promo = getattr(product, 'is_on_special', False)
    bg_color = (255, 255, 0) if (is_promo and 'Y' in color_scheme) else (255, 255, 255)
    draw.rectangle([0, 0, width, height], fill=bg_color)
    if not product: return

    # Black header bar
    draw.rectangle([0, 0, width, 25], fill=(0, 0, 0))
    name_font = get_font_by_type(14, "bold")
    draw.text((width//2, 12), product.name.upper()[:25], fill=(255,255,255), font=name_font, anchor="mm")

    # Massive Price
    price_str = f"${product.price}"
    price_font = get_dynamic_font_size(price_str, width - 20, height - 60, 55)
    p_color = (255, 0, 0) if ('R' in color_scheme) else (0,0,0)
    draw.text((width//2, height//2 + 5), price_str, fill=p_color, font=price_font, anchor="mm")

    supp_abbr = product.preferred_supplier.abbreviation if product.preferred_supplier else ""
    sku_text = f"{supp_abbr}: {product.sku}" if supp_abbr else f"{product.sku}"
    draw.text((width - 5, height - 5), sku_text, fill=(0,0,0), font=get_font_by_type(12, "condensed"), anchor="rb")
    if is_promo: draw.text((5, height - 5), "SPECIAL", fill=(0,0,0), font=get_font_by_type(20, "bold"), anchor="lb")

def template_v3(image, draw, product, width, height, color_scheme):
    """
    LAYOUT: MODERN / CLEAN (V3)
    ---------------------------
    Uses white space and a clean vertical split.
    """
    is_promo = getattr(product, 'is_on_special', False)
    split_x = int(width * 0.45)
    draw.rectangle([0, 0, split_x, height], fill=(255, 255, 255))
    right_bg = (255, 255, 0) if (is_promo and 'Y' in color_scheme) else (255, 255, 255)
    draw.rectangle([split_x, 0, width, height], fill=right_bg)
    if not product: return

    safe_pad = 8
    supp_abbr = product.preferred_supplier.abbreviation if product.preferred_supplier else ""
    sku_text = f"{supp_abbr}: {product.sku}" if supp_abbr else f"{product.sku}"
    draw.text((safe_pad, safe_pad), sku_text, fill=(0,0,0), font=get_font_by_type(10, "bold"))

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
    """
    MAIN RENDERER
    -------------
    Creates a PIL Image object for an ESL tag.
    This is called by the Celery task during the update lifecycle.
    """
    from .models import ESLTag
    try:
        if tag_instance:
            tag = tag_instance
        else:
            tag = ESLTag.objects.select_related('hardware_spec', 'paired_product__preferred_supplier').get(pk=tag_id)

        spec, product = tag.hardware_spec, tag.paired_product
        width, height = int(spec.width_px or 250), int(spec.height_px or 122)
        color_scheme = (spec.color_scheme or "BW").upper()

        # HARDWARE-ALIGNED RENDERING (as per working sandbox code)
        # 1. Create canvas with exact dimensions in RGB mode initially
        image = Image.new('RGB', (width, height), color=(255, 255, 255))
        draw = ImageDraw.Draw(image)

        # 2. Apply template
        tid = getattr(tag, 'template_id', 1)
        if tid == 3: template_v3(image, draw, product, width, height, color_scheme)
        elif tid == 2: template_v2(image, draw, product, width, height, color_scheme)
        else: template_v1(image, draw, product, width, height, color_scheme)

        # 3. Final alignment and resampling
        # Match user test code sequence exactly: convert("RGBA") THEN resize(..., LANCZOS)
        image = image.convert("RGBA")
        image = image.resize((width, height), Image.Resampling.LANCZOS)

        return image
    except Exception as e:
        logger.error(f"Critical error in generate_esl_image: {e}", exc_info=True)
        # Return a blank image as fallback (250x122 RGBA)
        fallback = Image.new('RGBA', (250, 122), color=(255, 255, 255, 255))
        return fallback

def trigger_bulk_sync(tag_ids):
    """
    TASK DISPATCHER: CELERY GROUP
    -----------------------------
    Takes a list of tag IDs and queues them all for refresh in the
    background as a single 'Group' of tasks.
    """
    from core.tasks import update_tag_image_task
    from .models import ESLTag

    # Filter only tags that have a product and hardware spec
    valid_tag_ids = list(ESLTag.objects.filter(id__in=tag_ids, paired_product__isnull=False, hardware_spec__isnull=False).values_list('id', flat=True))

    if not valid_tag_ids: return None

    # Create a Celery 'Group' - this allows us to track progress of the whole batch
    job_group = group(update_tag_image_task.s(tid) for tid in valid_tag_ids)
    result = job_group.apply_async()
    result.save() # Persist the group ID to the database so the UI can see it
    return result
