from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import openpyxl
from .models import Product, ESLTag

class BulkMapProcessor:
    """
    Processes a list of scans (SKUs and Tag IDs) to propose pairings.
    Expects a product scan followed by one or more tag scans.
    """
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
            product = Product.objects.filter(sku=code, store=self.store).first()
            tag = ESLTag.objects.filter(tag_mac=code, gateway__store=self.store).first()

            if product:
                pending_product = product
                continue

            if tag:
                if pending_product:
                    self.proposed.append({
                        'product_id': pending_product.id,
                        'product_name': pending_product.name,
                        'product_sku': pending_product.sku,
                        'tag_id': tag.id,
                        'tag_mac': tag.tag_mac,
                        'old_product': tag.paired_product.name if tag.paired_product else None
                    })
                else:
                    self.rejections.append({'line': line_num, 'code': code, 'reason': 'Orphaned Tag', 'note': 'No product scanned before this tag'})
                continue

            self.rejections.append({'line': line_num, 'code': code, 'reason': 'Unknown', 'note': 'Not a valid product or tag in this store'})
        return self.proposed, self.rejections

def process_modisoft_file_logic(file_path, active_store, user, commit=False):
    """
    Core logic for parsing Modisoft Excel files and updating products.
    Used for both preview and final commit.
    """
    results = {'new': [], 'update': [], 'rejected': [], 'unchanged_count': 0}
    try:
        wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
        sheet = wb.active
        headers = {str(cell.value).strip().lower(): idx for idx, cell in enumerate(sheet[1]) if cell.value}

        sku_idx = headers.get('scan code')
        name_idx = headers.get('item description')
        price_idx = headers.get('unit price') or headers.get('unit retail')

        if None in [sku_idx, name_idx, price_idx]:
            return None, "Missing required columns in Excel."

        for row in sheet.iter_rows(min_row=2, values_only=True):
            raw_sku = str(row[sku_idx]).strip() if row[sku_idx] else None
            raw_name = str(row[name_idx]).strip() if row[name_idx] else None
            raw_price = str(row[price_idx]).replace('$', '').replace(',', '').strip() if row[price_idx] else None

            if not all([raw_sku, raw_name, raw_price]):
                results['rejected'].append({'sku': raw_sku or "N/A", 'reason': "Incomplete data"})
                continue

            try:
                price_decimal = Decimal(raw_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            except InvalidOperation:
                results['rejected'].append({'sku': raw_sku, 'reason': f"Invalid price: {raw_price}"})
                continue

            product = Product.objects.filter(sku=raw_sku, store=active_store).first()
            if product:
                if product.price != price_decimal or product.name != raw_name:
                    results['update'].append({'sku': raw_sku, 'name': raw_name, 'new_price': price_decimal, 'old_price': product.price})
                    if commit:
                        product.name, product.price, product.updated_by = raw_name, price_decimal, user
                        product.save()
                else:
                    results['unchanged_count'] += 1
            else:
                results['new'].append({'sku': raw_sku, 'name': raw_name, 'new_price': price_decimal})
                if commit:
                    Product.objects.create(sku=raw_sku, name=raw_name, price=price_decimal, store=active_store, updated_by=user)
        return results, None
    except Exception as e:
        return None, f"Import error: {str(e)}"
