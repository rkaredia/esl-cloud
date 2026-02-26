import openpyxl
import re
import logging
import os
import io
import time
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.db import transaction
from django.core.files.storage import default_storage
from django.conf import settings
from openpyxl import Workbook

from .models import Store, ESLTag, Gateway, TagHardware, Product
from .services import BulkMapProcessor
from .middleware import InputSanitizationMiddleware

from .utils import template_v1, template_v2
from PIL import Image, ImageDraw
logger = logging.getLogger(__name__)


# =================================================================
# DECORATORS
# =================================================================

def store_required(view_func):
#    "\"\"Decorator to ensure a store is selected before accessing the view.\"\""
    @wraps(view_func)
    @login_required
    def _wrapped_view(request, *args, **kwargs):
        if not hasattr(request, 'active_store') or request.active_store is None:
            messages.warning(request, "Please select a store first.")
            return redirect('select_store')
        return view_func(request, *args, **kwargs)
    return _wrapped_view
 


# =================================================================
# STORE SELECTION VIEWS
# =================================================================

@login_required
def select_store(request):
#    \"\"\"Display store selection page based on user permissions.\"\"\"
    
    if request.user.is_superuser:
        user_stores = Store.objects.filter(is_active=True).order_by('name')
        user_company = None
    else:
        user_company = getattr(request.user, 'company', None)
        if not user_company:
            return render(request, 'admin/core/no_access.html', {
                'reason': "Your user account is not linked to any company. Please contact an Admin."
            })
        
        if request.user.role == 'owner':
            user_stores = Store.objects.filter(
                company=user_company, 
                is_active=True
            ).order_by('name')
        else:
            user_stores = request.user.managed_stores.filter(
                is_active=True
            ).order_by('name')

    if not user_stores.exists():
        return render(request, 'admin/core/no_access.html', {
            'reason': 'Your account has not been assigned to any specific stores yet.'
        })

    # Auto-select if only one store
    if user_stores.count() == 1:
        request.session['active_store_id'] = user_stores.first().id
        return redirect('admin:index')

    return render(request, 'admin/select_store.html', {
        'stores': user_stores,
        'user_company': user_company
    })


@login_required
def set_active_store(request, store_id):
#    \"\"\"Set the active store for the current session.\"\"\"
    
    if request.user.is_superuser:
        store = get_object_or_404(Store, id=store_id, is_active=True)
    else:
        user_company = getattr(request.user, 'company', None)
        if not user_company:
            raise PermissionDenied("User is not linked to a company.")

        if request.user.role == 'owner':
            store = get_object_or_404(
                Store, 
                id=store_id, 
                company=user_company,
                is_active=True
            )
        else:
            store = get_object_or_404(
                request.user.managed_stores.filter(is_active=True), 
                id=store_id
            )

    request.session['active_store_id'] = store.id
    return redirect('admin:index')


# =================================================================
# ESL TAG IMPORT VIEWS
# =================================================================

@login_required
def download_tag_template(request):
#    \"\"\"Download Excel template for ESL tag import.\"\"\"
    wb = Workbook()
    ws = wb.active
    ws.title = "ESL_Tag_Template"
    
    headers = ['tag_mac', 'gateway_mac', 'model_name']
    ws.append(headers)
    ws.append(['BE:01:02:03:04:05', 'FF:EE:DD:CC:BB:AA', 'Mi 05'])

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename=esl_tag_template.xlsx'
    wb.save(response)
    return response



@login_required
def preview_tag_import(request):
    """
    Processes tag imports without row limits. 
    Accepts 8-15 alphanumeric characters.
    """
    if request.method != 'POST' or not request.FILES.get('file'):
        return redirect('admin:core_esltag_changelist')
    
    active_store = getattr(request, 'active_store', None)
    if not active_store:
        messages.error(request, "Please select a store in the header first.")
        return redirect('admin:core_esltag_changelist')

    excel_file = request.FILES['file']
    
    try:
        # Load workbook in read-only mode for better performance with large files
        wb = openpyxl.load_workbook(excel_file, data_only=True, read_only=True)
        sheet = wb.active
    except Exception as e:
        messages.error(request, f"Error reading Excel: {str(e)}")
        return redirect('admin:core_esltag_changelist')
    
    summary = {'added': 0, 'updated': 0, 'rejected': 0, 'unchanged': 0}
    results = []
    
    # iterate_rows with read_only=True is very memory efficient
    for row_idx, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        
        # Handle rows that might be partially empty
        raw_tag_id = row[0] if len(row) > 0 else None
        raw_gw_mac = row[1] if len(row) > 1 else None
        model_name = row[2] if len(row) > 2 else None
        
        # Skip completely empty rows
        if not any([raw_tag_id, raw_gw_mac, model_name]):
            continue

        # 1. Clean the Tag ID (8-15 chars, Alpha-numeric only)
        sanitized_id = InputSanitizationMiddleware.sanitize_tag_id(raw_tag_id)
        
        if not sanitized_id:
            summary['rejected'] += 1
            results.append({
                'mac': str(raw_tag_id or "Empty"), 
                'status': 'rejected', 
                'message': f"Line {row_idx}: ID must be 8-15 alphanumeric characters (No symbols)."
            })
            continue

        # 2. Hardware Model Check
        spec = TagHardware.objects.filter(model_number=str(model_name or "").strip()).first()
        if not spec:
            summary['rejected'] += 1
            results.append({
                'mac': sanitized_id, 
                'status': 'rejected', 
                'message': f"Model '{model_name}' not recognized."
            })
            continue

        # 3. Gateway Check (Strictly within the active store)
        # We clean the Gateway input too just in case it has colons or dots
#        clean_gw = re.sub(r'[^0-9A-Za-z]', '', str(raw_gw_mac or ""))
        clean_gw = str(raw_gw_mac or "")
        gateway = Gateway.objects.filter(gateway_mac__iexact=clean_gw, store=active_store).first()
        
        if not gateway:
            summary['rejected'] += 1
            results.append({
                'mac': sanitized_id, 
                'status': 'rejected', 
                'message': f"Gateway '{raw_gw_mac}' not found in {active_store.name}."
            })
            continue

        # 4. Save/Update Logic
        tag, created = ESLTag.objects.get_or_create(
            tag_mac=sanitized_id,
            defaults={
                'gateway': gateway, 
                'hardware_spec': spec, 
                'updated_by': request.user
            }
        )

        if created:
            summary['added'] += 1
            status, msg = 'added', "New tag registered."
        else:
            # Check if assignment has changed
            if tag.gateway != gateway or tag.hardware_spec != spec:
                tag.gateway = gateway
                tag.hardware_spec = spec
                tag.updated_by = request.user
                tag.save()
                summary['updated'] += 1
                status, msg = 'updated', "Moved/Updated metadata."
            else:
                summary['unchanged'] += 1
                status, msg = 'unchanged', "No changes."
            
        results.append({'mac': sanitized_id, 'status': status, 'message': msg})

    return render(request, 'admin/core/esltag/import_preview.html', {
        'summary': summary,
        'results': results,
        'opts': ESLTag._meta,
    })
# =================================================================
# PRODUCT IMPORT VIEWS
# =================================================================

def process_modisoft_file(file_path, active_store, user, commit=False):
#    Parse Modisoft Excel and update Products with audit trail.
    results = {'new': [], 'update': [], 'rejected': [], 'unchanged_count': 0}
    
    try:
        wb = openpyxl.load_workbook(file_path, data_only=True,read_only=True)
        sheet = wb.active
       
        # Header mapping
        header_map = {
            str(cell.value).strip().lower(): idx 
            for idx, cell in enumerate(sheet[1]) if cell.value
        }
        
        sku_idx = header_map.get('scan code')
        name_idx = header_map.get('item description')
        price_idx = header_map.get('unit price') or header_map.get('unit retail')
        
        if None in [sku_idx, name_idx, price_idx]:
            missing = [
                k for k, v in {
                    'Scan code': sku_idx, 
                    'Item Description': name_idx, 
                    'Price': price_idx
                }.items() if v is None
            ]
            return None, f"Missing columns: {', '.join(missing)}"

        # Get bulk operation limit
        #max_rows = getattr(settings, 'BULK_OPERATION_LIMIT', 100)
        row_count = 0

        for row_idx, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            row_count += 1
            #if row_count > max_rows:
            #    break
                
            raw_sku = str(row[sku_idx]).strip() if row[sku_idx] else None
            raw_name = str(row[name_idx]).strip() if row[name_idx] else None
            raw_price = str(row[price_idx]).replace('$', '').replace(',', '').strip() if row[price_idx] else None

            if not all([raw_sku, raw_name, raw_price]):
                results['rejected'].append({
                    'row': row_idx, 
                    'sku': raw_sku or "N/A", 
                    'reason': "Incomplete data"
                })
                continue

            try:
                price_decimal = Decimal(raw_price).quantize(Decimal("0.00"))
            except InvalidOperation:
                results['rejected'].append({
                    'row': row_idx, 
                    'sku': raw_sku, 
                    'reason': f"Invalid price: {raw_price}"
                })
                continue

            product = Product.objects.filter(sku=raw_sku, store=active_store).first()
            
            if product:
                if product.price != price_decimal or product.name != raw_name:
                    results['update'].append({
                        'sku': raw_sku, 
                        'name': raw_name, 
                        'new_price': price_decimal, 
                        'old_price': product.price
                    })
                    if commit:
                        product.name = raw_name
                        product.price = price_decimal
                        product.updated_by = user
                        product.save()
                else:
                    results['unchanged_count'] += 1
            else:
                results['new'].append({
                    'sku': raw_sku, 
                    'name': raw_name, 
                    'new_price': price_decimal
                })
                if commit:
                    Product.objects.create(
                        sku=raw_sku, 
                        name=raw_name, 
                        price=price_decimal, 
                        store=active_store, 
                        updated_by=user
                    )
        
        return results, None

    except Exception as e:
        logger.exception("Modisoft import failure")
        return None, f"Import error. Please check the file format."


@login_required
def preview_product_import(request):
#    \"\"\"Handle multi-step Modisoft product import.\"\"\"
    active_store = getattr(request, 'active_store', None)
    if not active_store:
        messages.error(request, "Please select a store first.")
        return redirect('admin:core_product_changelist')

    if request.method == "POST":
        # Step 2: Confirm and Save
        if "confirm_save" in request.POST:
            temp_path = request.POST.get("temp_file_path")
            
            # Security: Validate temp path is within media root
            if not temp_path or not temp_path.startswith(settings.MEDIA_ROOT):
                messages.error(request, "Invalid file reference.")
                return redirect('admin:core_product_changelist')
            
            if not os.path.exists(temp_path):
                messages.error(request, "File not found. Please re-upload.")
                return redirect('admin:core_product_changelist')
            
            results, error = process_modisoft_file(temp_path, active_store, request.user, commit=True)
            if not error:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
                messages.success(
                    request, 
                    f"Imported {len(results['new'])} new, updated {len(results['update'])} products."
                )
                return redirect('admin:core_product_changelist')
            messages.error(request, error)

        # Step 1: Upload and Preview
        elif request.FILES.get("import_file"):
            myfile = request.FILES["import_file"]
            
            # Validate file size
            if myfile.size > 10 * 1024 * 1024:
                messages.error(request, "File too large. Maximum size is 10MB.")
                return redirect('admin:core_product_changelist')
            
            filename = default_storage.save(f'tmp/{myfile.name}', myfile)
            temp_path = os.path.join(settings.MEDIA_ROOT, filename)
            
            results, error = process_modisoft_file(temp_path, active_store, request.user, commit=False)
            if error:
                messages.error(request, error)
                return redirect('admin:core_product_changelist')
                
            return render(request, "admin/core/product/import_preview.html", {
                "title": "Product Import Preview",
                "results": results, 
                "temp_file_path": temp_path, 
                "store": active_store
            })

    return render(request, "admin/core/product/import_upload.html", {"store": active_store})


# =================================================================
# BULK MAPPING VIEW
# =================================================================

@login_required
def bulk_map_tags_view(request):
#    \"\"\"Handle bulk product-to-tag mapping from barcode scanner input.\"\"\"
    opts = ESLTag._meta
    context = {
        'opts': opts,
        'app_label': opts.app_label,
        'title': "Bulk Product-Tag Mapping",
    }

    if request.method == "POST":
        # Stage 2: Confirm mapping
        if 'confirm_mapping' in request.POST:
            proposed_data = request.session.get('pending_bulk_maps', [])
            
            # Limit bulk operations
            max_items = getattr(settings, 'BULK_OPERATION_LIMIT', 100)
            if len(proposed_data) > max_items:
                proposed_data = proposed_data[:max_items]
                messages.warning(request, f"Only first {max_items} mappings processed.")
            
            with transaction.atomic():
                for item in proposed_data:
                    try:
                        tag = ESLTag.objects.get(id=item['tag_id'])
                        tag.paired_product_id = item['product_id']
                        tag.updated_by = request.user
                        tag.save()
                    except ESLTag.DoesNotExist:
                        continue
            
            messages.success(request, f"Successfully mapped {len(proposed_data)} tags.")
            if 'pending_bulk_maps' in request.session:
                del request.session['pending_bulk_maps']
            return redirect("admin:core_esltag_changelist")

        # Stage 1: Parse file
        import_file = request.FILES.get('import_file')
        if not import_file:
            messages.error(request, "Please upload a text file.")
            return redirect(request.path)

        # Validate file size
        if import_file.size > 1024 * 1024:  # 1MB limit for text files
            messages.error(request, "File too large. Maximum size is 1MB.")
            return redirect(request.path)

        try:
            raw_text = import_file.read().decode('utf-8')
        except UnicodeDecodeError:
            messages.error(request, "Invalid file encoding. Please use UTF-8.")
            return redirect(request.path)
        
        store = getattr(request, 'active_store', None)
        if not store:
            messages.error(request, "Please select a store first.")
            return redirect('select_store')
        
        processor = BulkMapProcessor(raw_text, store, request.user)
        proposed, rejections = processor.process()

        # Store IDs in session
        request.session['pending_bulk_maps'] = proposed
        
        context.update({
            'proposed': proposed,
            'rejections': rejections,
            'stage': 'preview'
        })
        return render(request, 'admin/core/esltag/bulk_map_preview.html', context)

    return render(request, 'admin/core/esltag/bulk_map_upload.html', context)
