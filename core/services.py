from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import openpyxl
import logging
from django.db import transaction
from .models import Product, ESLTag

"""
SAIS CORE SERVICES: HIGH-PERFORMANCE DATA PROCESSING
----------------------------------------------------
While 'Views' handle the web request, 'Services' handle the heavy lifting
of data transformation and bulk updates.

If you are coming from a Data Warehouse background:
- These are your ETL (Extract, Transform, Load) routines.
- They are optimized to handle thousands of rows efficiently using
  'Bulk Operations' and 'In-Memory Lookups'.

Key Services:
1. BulkMapProcessor: Reconstructs Product-Tag pairings from scan logs.
2. process_modisoft_file_logic: Syncs the SAIS cloud with external POS pricing.
"""

logger = logging.getLogger(__name__)

class BulkMapProcessor:
    """
    LOGIC: SCANNER DATA RECONSTRUCTION
    ----------------------------------
    Processes a list of scans (SKUs and Tag IDs) to propose pairings.
    Expects a pattern: [PRODUCT SKU] followed by [TAG MAC].
    """
    def __init__(self, raw_text, store, user):
        # Convert raw text into a clean list of lines
        self.lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
        self.store = store
        self.user = user
        self.proposed = []
        self.rejections = []

    def process(self):
        """
        PERFORMANCE: O(N) Processing
        ----------------------------
        Instead of querying the database for every single scan line,
        we pre-fetch ALL involved products and tags into a memory dictionary
        (Hash Map) for instant lookup.
        """
        try:
            unique_codes = set(self.lines)

            # PRE-FETCH: Get everything we need in just 2 SQL queries
            products = {p.sku: p for p in Product.objects.filter(sku__in=unique_codes, store=self.store)}
            tags = {t.tag_mac: t for t in ESLTag.objects.select_related('paired_product').filter(tag_mac__in=unique_codes, gateway__store=self.store)}

            pending_product = None
            for index, code in enumerate(self.lines):
                line_num = index + 1
                product = products.get(code)
                tag = tags.get(code)

                # If the line is a Product SKU, remember it for the next Tag
                if product:
                    pending_product = product
                    continue

                # If the line is a Tag MAC, pair it with the last-seen Product
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
                        self.rejections.append({
                            'line': line_num,
                            'code': code,
                            'reason': 'Orphaned Tag',
                            'note': 'No product scanned before this tag'
                        })
                    continue

                # Not a product or a tag? Reject it.
                self.rejections.append({
                    'line': line_num,
                    'code': code,
                    'reason': 'Unknown',
                    'note': 'Not a valid product or tag in this store'
                })

            return self.proposed, self.rejections
        except Exception as e:
            logger.exception("Error in BulkMapProcessor.process")
            raise e

def process_modisoft_file_logic(file_path, active_store, user, commit=False):
    """
    ETL: POS PRICE SYNC
    -------------------
    Parses Modisoft Excel files (25k+ rows) and updates the SAIS product database.

    EDUCATIONAL: 'transaction.atomic()' ensures that if the server crashes
    halfway through, the entire update is rolled back. No partial data!
    """
    results = {'new': [], 'update': [], 'rejected': [], 'unchanged_count': 0}
    seen_skus = set()
    try:
        # PERFORMANCE: Load existing products into memory for O(1) lookup
        existing_products = {p.sku: p for p in Product.objects.filter(store=active_store)}

        # openpyxl: read_only=True is much faster and uses less memory for large files
        wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
        sheet = wb.active

        # Identify column indexes based on header names
        headers = {str(cell.value).strip().lower(): idx for idx, cell in enumerate(sheet[1]) if cell.value}
        sku_idx = headers.get('scan code')
        name_idx = headers.get('item description')
        price_idx = headers.get('unit price') or headers.get('unit retail')

        if None in [sku_idx, name_idx, price_idx]:
            return None, "Missing required columns in Excel (Scan Code, Item Description, Price)."

        # LOOP: Process every row in the spreadsheet
        for idx, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            try:
                raw_sku = str(row[sku_idx]).strip() if row[sku_idx] else None
                raw_name = str(row[name_idx]).strip() if row[name_idx] else None
                raw_price = str(row[price_idx]).replace('$', '').replace(',', '').strip() if row[price_idx] else None

                # VALIDATION
                if not all([raw_sku, raw_name, raw_price]):
                    results['rejected'].append({'row': idx, 'sku': raw_sku or "N/A", 'reason': "Incomplete data"})
                    continue

                if raw_sku in seen_skus:
                    results['rejected'].append({'row': idx, 'sku': raw_sku, 'reason': "Duplicate SKU in file"})
                    continue

                seen_skus.add(raw_sku)

                # DATA TYPING: Convert price string to Decimal for financial accuracy
                try:
                    price_decimal = Decimal(raw_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                except InvalidOperation:
                    results['rejected'].append({'row': idx, 'sku': raw_sku, 'reason': f"Invalid price format"})
                    continue

                # MATCHING: Check if product exists in SAIS
                product = existing_products.get(raw_sku)
                if product:
                    # If data is different, mark for update
                    if product.price != price_decimal or product.name != raw_name:
                        results['update'].append({'sku': raw_sku, 'name': raw_name, 'new_price': price_decimal, 'old_price': product.price})
                    else:
                        results['unchanged_count'] += 1
                else:
                    # Otherwise, mark as new
                    results['new'].append({'sku': raw_sku, 'name': raw_name, 'new_price': price_decimal})

            except Exception as row_error:
                logger.error(f"Error processing row in Modisoft import: {row_error}")

        # --- DB COMMIT PHASE ---
        if commit:
            with transaction.atomic():
                # 1. BULK UPDATE: Updates thousands of existing rows in one SQL command
                products_to_update = []
                for item in results['update']:
                    prod = existing_products[item['sku']]
                    prod.name, prod.price, prod.updated_by = item['name'], item['new_price'], user
                    products_to_update.append(prod)

                if products_to_update:
                    Product.objects.bulk_update(products_to_update, ['name', 'price', 'updated_by'])

                # 2. BULK CREATE: Inserts thousands of new rows in one SQL command
                products_to_create = []
                for item in results['new']:
                    products_to_create.append(Product(
                        sku=item['sku'], name=item['name'], price=item['new_price'],
                        store=active_store, updated_by=user
                    ))

                if products_to_create:
                    Product.objects.bulk_create(products_to_create)

                # 3. QUEUE HARDWARE UPDATES:
                # EDUCATIONAL: Django Signals don't run on bulk_update().
                # We must manually find all tags linked to these products and queue
                # their image refresh in Celery.
                updated_skus = [item['sku'] for item in results['update']]
                if updated_skus:
                    from core.tasks import update_tag_image_task

                    # Find all affected Tag IDs
                    tag_ids = ESLTag.objects.filter(
                        paired_product__sku__in=updated_skus,
                        store=active_store
                    ).values_list('id', flat=True)

                    # Queue the tasks AFTER the DB transaction is successful
                    for tid in tag_ids:
                        transaction.on_commit(lambda current_tid=tid: update_tag_image_task.delay(current_tid))

        return results, None
    except Exception as e:
        logger.exception(f"Modisoft import failure for file {file_path}")
        return None, f"Import error: A technical issue occurred while reading the file."
