    # core/views.py
import openpyxl
import re
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from .models import Store
from django.contrib import messages
from django.shortcuts import redirect, get_object_or_404
from django.core.exceptions import PermissionDenied
# core/views.py
from django.http import HttpResponse
from openpyxl import Workbook
from .models import ESLTag, Gateway, TagHardware, Product # Added HardwareSpec

import logging
from decimal import Decimal, InvalidOperation
import os
from django.core.files.storage import default_storage
from django.conf import settings

# Set up logging
logger = logging.getLogger(__name__)


@login_required
def set_active_store(request, store_id):
# OLD LOGIC (causing 404):
    # store = get_object_or_404(Store, id=store_id, company=request.user.company)

    # NEW LOGIC (Superuser friendly):
    if request.user.is_superuser:
        # Superusers can access ANY store in the database
        store = get_object_or_404(Store, id=store_id)
    else:
        # Regular users are strictly filtered by their company
        store = get_object_or_404(Store, id=store_id, company=request.user.company)

    # Save the store ID in the session
    request.session['active_store_id'] = store.id
    
    # Redirect back to the page you were on
    return redirect(request.META.get('HTTP_REFERER', '/admin/'))




@login_required
def select_store(request):
    # 1. Global Bypass for Superusers
    if request.user.is_superuser:
        from .models import Store
        user_stores = Store.objects.all().order_by('name')
        user_company = None # Superusers don't need a company context
    else:
        # 2. Tenant Security: Check for Company link
        user_company = getattr(request.user, 'company', None)
        if not user_company:
            return render(request, 'admin/core/no_access.html', {
                'reason': "Your user account is not linked to any company. Please contact an Admin."
            })
        
        # 3. Role-Based Store Filtering
        if request.user.role == 'owner':
            from .models import Store
            user_stores = Store.objects.filter(company=user_company).order_by('name')
        else:
            # Managers/Staff only see stores they are manually assigned to
            user_stores = request.user.managed_stores.all().order_by('name')

    # 4. Handle "No Stores Found" (After role-based filtering)
    if not user_stores.exists():
        return render(request, 'admin/core/no_access.html', {
            'reason': 'Your account has not been assigned to any specific stores yet.'
        })

    # 5. Efficiency: Auto-select if only 1 store exists
    if user_stores.count() == 1:
        request.session['active_store_id'] = user_stores.first().id
        return redirect('admin:index')

    # 6. Final Render for Multiple Stores
    return render(request, 'admin/select_store.html', {
        'stores': user_stores,
        'user_company': user_company
    })

  

@login_required
def set_active_store(request, store_id):
    from .models import Store
    
    # 1. SUPERUSER BYPASS
    if request.user.is_superuser:
        # Superusers can manage any store in the system
        store = get_object_or_404(Store, id=store_id)
    
    else:
        # 2. TENANT SECURITY
        user_company = getattr(request.user, 'company', None)
        if not user_company:
            # Safety check: if no company, they shouldn't be here
            raise PermissionDenied("User is not linked to a company.")

        if request.user.role == 'owner':
            # Owners can select any store within their company
            store = get_object_or_404(Store, id=store_id, company=user_company)
        else:
            # Managers can only select stores they are manually assigned to
            store = get_object_or_404(request.user.managed_stores.all(), id=store_id)

    # 3. SESSION PERSISTENCE
    request.session['active_store_id'] = store.id
    
    # Redirect back to the Admin Dashboard (or wherever they came from)
    return redirect('admin:index')


@login_required
def store_required(view_func):
    def _wrapped_view(request, *args, **kwargs):
        if not hasattr(request, 'active_store') or request.active_store is None:
            return redirect('select_store')
        return view_func(request, *args, **kwargs)
    return _wrapped_view    




def download_tag_template(request):
    wb = Workbook()
    ws = wb.active
    ws.title = "ESL_Tag_Template"
    
    # Headers - Only the essentials
    headers = ['tag_mac', 'gateway_mac', 'model_name']
    ws.append(headers)
    
    # Sample Row
    # Note: 'model_name' MUST match a name in your HardwareSpec table (e.g., 'Mi 05')
    ws.append(['BE:01:02:03:04:05', 'FF:EE:DD:CC:BB:AA', 'Mi 05'])  

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=esl_tag_template.xlsx'
    wb.save(response)
    return response

# core/views.py logic snippet
def preview_tag_import(request):
    if request.method == 'POST' and request.FILES.get('file'):
        active_store = getattr(request, 'active_store', None)
        if not active_store:
            messages.error(request, "Please select a store first.")
            return redirect('admin:core_esltag_changelist')

        excel_file = request.FILES['file']
        wb = openpyxl.load_workbook(excel_file)
        sheet = wb.active
        
        summary = {'added': 0, 'updated': 0, 'rejected': 0, 'unchanged': 0}
        results = []

        # MAC Address regex for basic format validation
        mac_regex = re.compile(r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$|^[0-9A-Fa-f]{12}$')

        for row_idx, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            # 1. Unpack and handle potential missing columns
            try:
                tag_mac, gw_mac, model_name = row[0:3]
            except (ValueError, IndexError):
                summary['rejected'] += 1
                results.append({'mac': 'N/A', 'status': 'rejected', 'message': f"Line {row_idx}: Row is incomplete."})
                continue

            # 2. STRICT VALIDATION: Check for empty values
            if not all([tag_mac, gw_mac, model_name]):
                summary['rejected'] += 1
                results.append({
                    'mac': str(tag_mac or 'Unknown'), 
                    'status': 'rejected', 
                    'message': f"Line {row_idx}: Missing required data (MAC, Gateway, or Model)."
                })
                continue

            # 3. FORMAT VALIDATION: Check Tag MAC format
            if not mac_regex.match(str(tag_mac)):
                summary['rejected'] += 1
                results.append({
                    'mac': str(tag_mac), 'status': 'rejected', 
                    'message': "Invalid MAC format. Must be 12 hex chars."
                })
                continue

            # 4. DATABASE VALIDATION: Hardware Spec
            spec = TagHardware.objects.filter(model_number=str(model_name).strip()).first()
            if not spec:
                summary['rejected'] += 1
                results.append({
                    'mac': str(tag_mac), 'status': 'rejected', 
                    'message': f"Hardware Model '{model_name}' not found. Add it to Specs first."
                })
                continue

            # 5. DATABASE VALIDATION: Gateway
            gateway = Gateway.objects.filter(gateway_mac=str(gw_mac).strip(), store=active_store).first()
            if not gateway:
                summary['rejected'] += 1
                results.append({
                    'mac': str(tag_mac), 'status': 'rejected', 
                    'message': f"Gateway {gw_mac} not found in {active_store.name}."
                })
                continue

            # 6. PROCESSING: If it reaches here, the data is ROBUST
            tag = ESLTag.objects.filter(tag_mac=tag_mac).first()
            if tag:
                has_changed = (tag.gateway != gateway or tag.hardware_spec != spec)
                if has_changed:
                    tag.gateway = gateway
                    tag.hardware_spec = spec
                    tag.updated_by = request.user  # <--- TRACK THE USER HERE
                    tag.save()
                    summary['updated'] += 1
                    status, msg = 'updated', "Metadata updated."
                else:
                    summary['unchanged'] += 1
                    status, msg = 'unchanged', "Already up to date."
            else:
                # CREATE NEW with audit tracking
                ESLTag.objects.create(
                    tag_mac=tag_mac, 
                    gateway=gateway, 
                    hardware_spec=spec,
                    updated_by=request.user  # <--- TRACK THE USER HERE
                )
                summary['added'] += 1
                status, msg = 'added', "New tag registered."
                
            results.append({'mac': tag_mac, 'status': status, 'message': msg})

        return render(request, 'admin/core/esltag/import_preview.html', {
            'summary': summary,
            'results': results,
            'opts': ESLTag._meta,
        })

    return redirect('admin:core_esltag_changelist')


def process_modisoft_file(file_path, active_store, user, commit=False):
    """
    Modular helper to parse Modisoft Excel and update Products.
    Includes audit trail for 'updated_by'.
    """
    results = {'new': [], 'update': [], 'rejected': [], 'unchanged_count': 0}
    
    try:
        wb = openpyxl.load_workbook(file_path, data_only=True)
        sheet = wb.active
        
        # 1. Header Mapping
        header_map = {str(cell.value).strip().lower(): idx for idx, cell in enumerate(sheet[1]) if cell.value}
        
        try:
            sku_idx = header_map.get('scan code')
            name_idx = header_map.get('item description')
            price_idx = header_map.get('unit price') or header_map.get('unit retail')
            
            if None in [sku_idx, name_idx, price_idx]:
                missing = [k for k, v in {'Scan code': sku_idx, 'Item Description': name_idx, 'Price': price_idx}.items() if v is None]
                return None, f"Missing columns: {', '.join(missing)}"
        except Exception as e:
            logger.error(f"Header mapping error: {e}")
            return None, "Invalid file format."

        seen_skus = set()

        # 2. Row Processing
        for row_idx, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            raw_sku = str(row[sku_idx]).strip() if row[sku_idx] else None
            raw_name = str(row[name_idx]).strip() if row[name_idx] else None
            raw_price = str(row[price_idx]).replace('$', '').replace(',', '').strip() if row[price_idx] else None

            if not all([raw_sku, raw_name, raw_price]):
                results['rejected'].append({'row': row_idx, 'sku': raw_sku or "N/A", 'reason': "Incomplete data"})
                continue

            try:
                price_decimal = Decimal(raw_price).quantize(Decimal("0.00"))
            except InvalidOperation:
                results['rejected'].append({'row': row_idx, 'sku': raw_sku, 'reason': f"Bad Price: {raw_price}"})
                continue

            # Database Operation
            product = Product.objects.filter(sku=raw_sku, store=active_store).first()
            
            if product:
                if product.price != price_decimal or product.name != raw_name:
                    results['update'].append({'sku': raw_sku, 'name': raw_name, 'new_price': price_decimal, 'old_price': product.price})
                    if commit:
                        product.name = raw_name
                        product.price = price_decimal
                        product.updated_by = user  # FIX: Audit trail
                        product.save()
                else:
                    results['unchanged_count'] += 1
            else:
                results['new'].append({'sku': raw_sku, 'name': raw_name, 'new_price': price_decimal})
                if commit:
                    Product.objects.create(
                        sku=raw_sku, name=raw_name, price=price_decimal, 
                        store=active_store, updated_by=user # FIX: Audit trail
                    )
        
        return results, None

    except Exception as e:
        logger.exception("Modisoft import critical failure")
        return None, f"System error: {str(e)}"

def preview_product_import(request):
    """View to handle the multi-step Modisoft import process."""
    active_store = getattr(request, 'active_store', None)
    if not active_store:
        messages.error(request, "Please select a store first.")
        return redirect('admin:core_product_changelist')

    if request.method == "POST":
        # Step 2: Confirm and Save
        if "confirm_save" in request.POST:
            temp_path = request.POST.get("temp_file_path")
            results, error = process_modisoft_file(temp_path, active_store, request.user, commit=True)
            if not error:
                os.remove(temp_path)
                messages.success(request, f"Imported {len(results['new'])} new, updated {len(results['update'])} products.")
                return redirect('admin:core_product_changelist')
            messages.error(request, error)

        # Step 1: Upload and Preview
        elif request.FILES.get("import_file"):
            myfile = request.FILES["import_file"]
            filename = default_storage.save(f'tmp/{myfile.name}', myfile)
            temp_path = os.path.join(settings.MEDIA_ROOT, filename)
            
            results, error = process_modisoft_file(temp_path, active_store, request.user, commit=False)
            if error:
                messages.error(request, error)
                return redirect('admin:core_product_changelist')
                
            #return render(request, "admin/core/product/import_preview.html", {
            #    "results": results, "temp_file_path": temp_path, "store": active_store
            #})
            return render(request, "admin/core/product/import_preview.html", {
                "title": f"Product Import Preview",
                "results": results, 
                "temp_file_path": temp_path, 
                "store": active_store
            })  

    return render(request, "admin/core/product/import_upload.html", {"store": active_store})
    # Inside preview_product_import in views.py
  