import openpyxl
from decimal import Decimal, InvalidOperation
from .models import Product, ESLTag

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

#----------------------------------------------------------------------------
#        MAPPING PRODUCTS TO TAG VIA FILE IMPORT
#----------------------------------------------------------------------------


# core/services.py
from .models import Product, ESLTag

class BulkMapProcessor:
    def __init__(self, raw_text, store, user):
        self.lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
        self.store = store
        self.user = user
        self.proposed = []
        self.rejections = []

    def process(self):
        pending_product = None
        
        for index, code in enumerate(self.lines):
            line_num = index + 1
            
            # Lookups restricted to the active store
            product = Product.objects.filter(sku=code, store=self.store).first()
            tag = ESLTag.objects.filter(tag_mac=code, gateway__store=self.store).first()

            if product:
                if pending_product:
                    self.rejections.append({
                        'line': line_num, 'code': pending_product.sku,
                        'reason': 'Overwritten', 'note': f'Followed by product {product.sku}'
                    })
                pending_product = product
                continue

            if tag:
                if pending_product:
                    # SUCCESS PAIR
                    self.proposed.append({
                        'product_id': pending_product.id,
                        'product_name': pending_product.name,
                        'product_sku': pending_product.sku,
                        'tag_id': tag.id,
                        'tag_mac': tag.tag_mac,
                        'old_product': tag.paired_product.name if tag.paired_product else None
                    })
                    pending_product = None # Reset
                else:
                    self.rejections.append({
                        'line': line_num, 'code': code,
                        'reason': 'Orphaned Tag', 'note': 'No product scanned before this tag'
                    })
                continue

            # If it's neither, it's garbage. 
            # Per our rule: Garbage kills the pending product for safety.
            if pending_product:
                self.rejections.append({
                    'line': line_num, 'code': pending_product.sku,
                    'reason': 'Safety Abort', 'note': f'Product dropped because next scan ({code}) was invalid'
                })
                pending_product = None
            
            self.rejections.append({
                'line': line_num, 'code': code,
                'reason': 'Unknown/Foreign', 'note': 'Not a valid product or tag in this store'
            })

        return self.proposed, self.rejections