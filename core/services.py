import openpyxl
from decimal import Decimal, InvalidOperation
from .models import Product

def get_column_map(sheet):
    """
    Helper function to find the index of required columns by their name.
    This allows the Excel file to have columns in any order.
    """
    # Grab the first row (headers) and clean the text
    headers = [str(cell.value).strip() if cell.value else "" for cell in sheet[1]]
    
    try:
        mapping = {
            'sku': headers.index("Scan Code"),
            'name': headers.index("Item Description"),
            'price': headers.index("Unit Retail")
        }
        return mapping
    except ValueError as e:
        # If one of the required columns is missing, raise an error
        missing_col = str(e).split("'")[1]
        raise ValueError(f"Required column '{missing_col}' not found in Excel file.")

def preview_modisoft_xlsx(file, store):
    """
    Analyzes the Excel file and compares it against existing database records.
    Returns a report without saving any data.
    """
    import openpyxl
    from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
    from .models import Product

    # 1. Load Workbook
    wb = openpyxl.load_workbook(file, data_only=True)
    sheet = wb.active
    
    # 2. Map Headers (Allows columns to be in any order)
    try:
        col_map = get_column_map(sheet)
    except ValueError as e:
        raise ValueError(str(e))

    # Constants for rounding
    TWO_PLACES = Decimal('0.01')

    report = {
        'new_products': [],
        'price_updates': [],
        'ignored_items': [] 
    }

    # 3. Process Rows (Starting from row 2 to skip headers)
    for row_idx, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        # Extract raw values using the map
        sku_val = row[col_map['sku']]
        name_val = row[col_map['name']]
        price_val = row[col_map['price']]
        
        # Sanitize text: Convert to string and strip spaces, handle Python None objects
        sku = str(sku_val).strip() if sku_val is not None else ""
        name = str(name_val).strip() if name_val is not None else ""
        
        reasons = []
        clean_price = None

        # --- VALIDATION BLOCK ---
        # Reject if SKU is empty or the literal word "None"
        if not sku or sku.lower() == "none":
            reasons.append("Missing Scan Code")
            
        # Reject if Name is empty or the literal word "None"
        if not name or name.lower() == "none":
            reasons.append("Missing Description")
        
        # Reject if Price is missing, non-numeric, or zero
        if price_val is None or str(price_val).strip() == "":
            reasons.append("Missing Unit Retail")
        else:
            try:
                # Clean currency string (remove $ and spaces)
                raw_price_str = str(price_val).replace('$', '').strip()
                # Round to 2 decimal places immediately (e.g., 0.106 -> 0.11)
                clean_price = Decimal(raw_price_str).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
                
                if clean_price <= 0:
                    reasons.append("Price must be greater than zero")
            except (InvalidOperation, ValueError):
                reasons.append(f"Invalid Price Format: '{price_val}'")

        # --- REJECTION HANDLING ---
        if reasons:
            report['ignored_items'].append({
                'row_number': row_idx,
                'sku': sku if sku and sku.lower() != "none" else "N/A",
                'name': name if name and name.lower() != "none" else "N/A",
                'reason': ", ".join(reasons)
            })
            continue 

        # --- DATABASE COMPARISON ---
        # Search for this SKU specifically within the selected Store
        existing_product = Product.objects.filter(sku=sku, store=store).first()

        if not existing_product:
            # Category A: Brand New Item
            report['new_products'].append({
                'sku': sku,
                'name': name,
                'price': clean_price
            })
        else:
            # Category B: Existing Item - Check for Price Change
            # Both are now Decimal(2 places), so 0.11 == 0.11
            if existing_product.price != clean_price:
                report['price_updates'].append({
                    'sku': sku,
                    'name': name,
                    'old_price': existing_product.price,
                    'new_price': clean_price
                })
            
    return report


def process_modisoft_xlsx(file, store):
    import openpyxl
    from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
    from .models import Product

    wb = openpyxl.load_workbook(file, data_only=True)
    sheet = wb.active
    col_map = get_column_map(sheet)
    TWO_PLACES = Decimal('0.01')
    count = 0

    for row in sheet.iter_rows(min_row=2, values_only=True):
        # 1. Extraction (Matches Preview)
        sku_val = row[col_map['sku']]
        name_val = row[col_map['name']]
        price_val = row[col_map['price']]

        sku = str(sku_val).strip() if sku_val is not None else ""
        name = str(name_val).strip() if name_val is not None else ""

        # 2. Validation & Precision (Matches Preview)
        if not sku or sku.lower() == "none" or not name or name.lower() == "none":
            continue

        try:
            if price_val is None: continue
            raw_price_str = str(price_val).replace('$', '').strip()
            # Force exactly 2 decimal places before saving
            clean_price = Decimal(raw_price_str).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            
            if clean_price <= 0: continue

            # 3. Atomic Update/Create
            obj, created = Product.objects.update_or_create(
                sku=sku,
                store=store,
                defaults={
                    'name': name,
                    'price': clean_price # This is now a clean Decimal
                }
            )
            count += 1
        except (InvalidOperation, ValueError):
            continue
            
    return count