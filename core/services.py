from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import openpyxl
import logging
from django.db import transaction
from .models import Product, ESLTag

logger = logging.getLogger(__name__)

class BulkMapProcessor:
    """
    Processes a list of scans (SKUs and Tag IDs) to propose pairings.
    Optimized with in-memory lookups to handle large batch scans.
    """
    def __init__(self, raw_text, store, user):
        self.lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
        self.store = store
        self.user = user
        self.proposed = []
        self.rejections = []

    def process(self):
        try:
            # Prefetch all relevant products and tags for this store
            unique_codes = set(self.lines)
            products = {p.sku: p for p in Product.objects.filter(sku__in=unique_codes, store=self.store)}
            tags = {t.tag_mac: t for t in ESLTag.objects.select_related('paired_product').filter(tag_mac__in=unique_codes, gateway__store=self.store)}

            pending_product = None
            for index, code in enumerate(self.lines):
                line_num = index + 1
                product = products.get(code)
                tag = tags.get(code)

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
        except Exception as e:
            logger.exception("Error in BulkMapProcessor.process")
            raise e

def process_modisoft_file_logic(file_path, active_store, user, commit=False):
    """
    Core logic for parsing Modisoft Excel files and updating products.
    Optimized for large files (25k+ rows) using in-memory lookups and transactions.
    """
    results = {'new': [], 'update': [], 'rejected': [], 'unchanged_count': 0}
    seen_skus = set()
    try:
        # Load existing products into memory for O(1) lookup
        existing_products = {p.sku: p for p in Product.objects.filter(store=active_store)}

        wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
        sheet = wb.active
        headers = {str(cell.value).strip().lower(): idx for idx, cell in enumerate(sheet[1]) if cell.value}

        sku_idx = headers.get('scan code')
        name_idx = headers.get('item description')
        price_idx = headers.get('unit price') or headers.get('unit retail')

        if None in [sku_idx, name_idx, price_idx]:
            return None, "Missing required columns in Excel (Scan Code, Item Description, Price)."

        for idx, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            try:
                raw_sku = str(row[sku_idx]).strip() if row[sku_idx] else None
                raw_name = str(row[name_idx]).strip() if row[name_idx] else None
                raw_price = str(row[price_idx]).replace('$', '').replace(',', '').strip() if row[price_idx] else None

                if not all([raw_sku, raw_name, raw_price]):
                    results['rejected'].append({
                        'row': idx,
                        'sku': raw_sku or "N/A",
                        'name': raw_name or "N/A",
                        'price': raw_price or "N/A",
                        'reason': "Incomplete data"
                    })
                    continue

                if raw_sku in seen_skus:
                    results['rejected'].append({
                        'row': idx,
                        'sku': raw_sku,
                        'name': raw_name,
                        'price': raw_price,
                        'reason': "Duplicate SKU in file"
                    })
                    continue

                seen_skus.add(raw_sku)

                try:
                    price_decimal = Decimal(raw_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                except InvalidOperation:
                    results['rejected'].append({
                        'row': idx,
                        'sku': raw_sku,
                        'name': raw_name,
                        'price': raw_price,
                        'reason': f"Invalid price format"
                    })
                    continue

                product = existing_products.get(raw_sku)
                if product:
                    if product.price != price_decimal or product.name != raw_name:
                        results['update'].append({'sku': raw_sku, 'name': raw_name, 'new_price': price_decimal, 'old_price': product.price})
                    else:
                        results['unchanged_count'] += 1
                else:
                    results['new'].append({'sku': raw_sku, 'name': raw_name, 'new_price': price_decimal})
            except Exception as row_error:
                logger.error(f"Error processing row in Modisoft import: {row_error}")
                results['rejected'].append({
                    'row': idx,
                    'sku': "Unknown",
                    'name': "Unknown",
                    'price': "N/A",
                    'reason': "System error processing row"
                })

        if commit:
            with transaction.atomic():
                # Perform the actual DB operations in a single transaction
                for item in results['update']:
                    prod = existing_products[item['sku']]
                    prod.name, prod.price, prod.updated_by = item['name'], item['new_price'], user
                    prod.save() # Note: .save() triggers individual tag updates.

                for item in results['new']:
                    Product.objects.create(
                        sku=item['sku'], name=item['name'], price=item['new_price'],
                        store=active_store, updated_by=user
                    )

        return results, None
    except Exception as e:
        logger.exception(f"Modisoft import failure for file {file_path}")
        return None, f"Import error: A technical issue occurred while reading the file."
