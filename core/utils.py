import os
import textwrap
from io import BytesIO
from django.core.files.base import ContentFile
from django.utils import timezone
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont
import barcode
from barcode.writer import ImageWriter

def get_font(size):
    """Loads ArialBold.ttf from the core/static/fonts directory."""
    font_path = os.path.join(settings.BASE_DIR, 'core', 'static', 'fonts', 'ArialBold.ttf')
    try:
        return ImageFont.truetype(font_path, size)
    except:
        return ImageFont.load_default()

def get_dynamic_font_size(text, max_w, max_h, initial_size=95):
    """Scales font down to fit specific dimensions."""
    size = initial_size
    font = get_font(size)
    if not hasattr(font, 'getbbox'): return font

    d = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    while size > 10:
        bbox = d.textbbox((0, 0), text, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if w <= max_w and h <= max_h:
            break
        size -= 2
        font = get_font(size)
    return font

def generate_esl_image(tag):
    if not tag.paired_product:
        return None

    product = tag.paired_product
    width, height = 296, 128
    image = Image.new('RGB', (width, height), color='white')
    draw = ImageDraw.Draw(image)

    # --- 1. GEOMETRY ---
    left_limit = int(width * 0.60)  # 177px
    red_box_w = width - left_limit  # 119px
    padding = 8

    # --- 2. RIGHT SIDE: RED PRICE BLOCK (Superscript Cents) ---
    draw.rectangle([left_limit, 0, width, height], fill='red')
    
    # Split price into Dollars and Cents
    price_val = float(product.price)
    price_parts = f"{price_val:.2f}".split('.')
    dollars_str = f"${price_parts[0]}"
    cents_str = price_parts[1]

    # Calculate Dollar font size (Max width 85px to leave room for cents)
    d_font = get_dynamic_font_size(dollars_str, red_box_w - 40, height - 20, initial_size=90)
    # Cents font is roughly 45% of the dollar size
    c_font = get_font(int(d_font.size * 0.45))

    # Measure for centering
    d_bbox = draw.textbbox((0, 0), dollars_str, font=d_font)
    c_bbox = draw.textbbox((0, 0), cents_str, font=c_font)
    
    d_w = d_bbox[2] - d_bbox[0]
    c_w = c_bbox[2] - c_bbox[0]
    total_price_w = d_w + c_w + 2
    
    # Starting X to center the combined string in the red box
    price_x_start = left_limit + (red_box_w - total_price_w) / 2
    center_y = height / 2

    # Draw Dollars (Middle-Left anchor)
    draw.text((price_x_start, center_y), dollars_str, fill='white', font=d_font, anchor="lm")
    
    # Draw Cents (Raised up)
    # Offset Y by about 25% of the dollar height to create the superscript look
    cents_y = center_y - (d_bbox[3] - d_bbox[1]) * 0.2
    draw.text((price_x_start + d_w + 2, cents_y), cents_str, fill='white', font=c_font, anchor="lm")

    # --- 3. LEFT SIDE: CLIPPED PRODUCT INFO (With Divider) ---
    left_layer = Image.new('RGBA', (left_limit, height), (255, 255, 255, 0))
    l_draw = ImageDraw.Draw(left_layer)

    # SKU
    sku_font = get_font(18)
    l_draw.text((padding, 8), str(product.sku), fill='black', font=sku_font)

    # DIVIDER LINE (Enhancement #2)
    l_draw.line([(padding, 30), (left_limit - padding, 30)], fill='black', width=1)

    # Product Name
    name_font = get_font(22)
    wrapper = textwrap.TextWrapper(width=15)
    name_lines = wrapper.wrap(text=product.name)
    
    y_text = 38
    for line in name_lines[:2]:
        l_draw.text((padding, y_text), line, fill='black', font=name_font)
        y_text += 26

    # Barcode
    if product.sku:
        try:
            code128 = barcode.get_barcode_class('code128')
            ean = code128(str(product.sku), writer=ImageWriter())
            barcode_img = ean.render(writer_options={"module_height": 10, "module_width": 0.2, "font_size": 1, "quiet_zone": 1})
            barcode_img = barcode_img.resize((left_limit - 20, 32)).convert("RGBA")
            left_layer.paste(barcode_img, (padding, height - 38))
        except:
            pass

    # Composite layers
    image.paste(left_layer, (0, 0), left_layer)

    # --- 4. SAVE ---
    filename = f"{tag.tag_mac}.png"
    temp_handle = BytesIO()
    image.save(temp_handle, format='PNG')
    tag.tag_image.save(filename, ContentFile(temp_handle.getvalue()), save=False)
    tag.last_updated = timezone.now()
    tag.save()
    temp_handle.close()
    
    return tag.tag_image.url