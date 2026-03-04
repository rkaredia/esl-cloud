import logging
import os
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
import openpyxl

from .models import Store, ESLTag, Gateway, TagHardware, Product
from .services import BulkMapProcessor, process_modisoft_file_logic
from .middleware import InputSanitizationMiddleware

logger = logging.getLogger(__name__)

# --- DECORATORS ---

def store_required(view_func):
    """Decorator to ensure a store is selected before accessing the view."""
    @wraps(view_func)
    @login_required
    def _wrapped_view(request, *args, **kwargs):
        if not hasattr(request, 'active_store') or request.active_store is None:
            messages.warning(request, "Please select a store first.")
            return redirect('select_store')
        return view_func(request, *args, **kwargs)
    return _wrapped_view

# --- STORE SELECTION ---

@login_required
def select_store(request):
    """Displays the store selection page based on user permissions."""
    if request.user.is_superuser:
        user_stores = Store.objects.filter(is_active=True).order_by('name')
        user_company = None
    else:
        user_company = getattr(request.user, 'company', None)
        if not user_company:
            return render(request, 'admin/core/no_access.html', {'reason': "User account not linked to any company."})
        
        if request.user.role == 'owner':
            user_stores = Store.objects.filter(company=user_company, is_active=True).order_by('name')
        else:
            user_stores = request.user.managed_stores.filter(is_active=True).order_by('name')

    if not user_stores.exists():
        return render(request, 'admin/core/no_access.html', {'reason': 'No stores assigned to your account.'})

    if user_stores.count() == 1:
        request.session['active_store_id'] = user_stores.first().id
        return redirect('admin:index')

    return render(request, 'admin/select_store.html', {'stores': user_stores, 'user_company': user_company})

@login_required
def set_active_store(request, store_id):
    """Sets the active store for the current session."""
    if request.user.is_superuser:
        store = get_object_or_404(Store, id=store_id, is_active=True)
    else:
        user_company = getattr(request.user, 'company', None)
        if request.user.role == 'owner':
            store = get_object_or_404(Store, id=store_id, company=user_company, is_active=True)
        else:
            store = get_object_or_404(request.user.managed_stores.filter(is_active=True), id=store_id)

    request.session['active_store_id'] = store.id
    return redirect('admin:index')

# --- IMPORT VIEWS ---

@login_required
def download_tag_template(request):
    """Generates and downloads an Excel template for ESL tag imports."""
    wb = Workbook()
    ws = wb.active
    ws.append(['tag_mac', 'gateway_mac', 'model_name'])
    ws.append(['BE:01:02:03:04:05', 'FF:EE:DD:CC:BB:AA', 'Mi 05'])
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=esl_tag_template.xlsx'
    wb.save(response)
    return response

@login_required
def preview_tag_import(request):
    """Processes tag imports from an Excel file and provides a preview."""
    if request.method != 'POST' or not request.FILES.get('file'):
        return redirect('admin:core_esltag_changelist')
    
    active_store = getattr(request, 'active_store', None)
    if not active_store:
        messages.error(request, "Select a store first.")
        return redirect('admin:core_esltag_changelist')

    try:
        wb = openpyxl.load_workbook(request.FILES['file'], data_only=True, read_only=True)
        sheet = wb.active
    except Exception as e:
        messages.error(request, f"Error reading Excel: {str(e)}")
        return redirect('admin:core_esltag_changelist')
    
    summary = {'added': 0, 'updated': 0, 'rejected': 0, 'unchanged': 0}
    results = []
    
    for row in sheet.iter_rows(min_row=2, values_only=True):
        if not any(row[:3]): continue
        sanitized_id = InputSanitizationMiddleware.sanitize_tag_id(row[0])
        spec = TagHardware.objects.filter(model_number=str(row[2] or "").strip()).first()
        gateway = Gateway.objects.filter(gateway_mac__iexact=str(row[1] or ""), store=active_store).first()
        
        if not sanitized_id or not spec or not gateway:
            summary['rejected'] += 1
            results.append({'mac': str(row[0]), 'status': 'rejected', 'message': 'Invalid ID, Model, or Gateway.'})
            continue

        tag, created = ESLTag.objects.get_or_create(tag_mac=sanitized_id, defaults={'gateway': gateway, 'hardware_spec': spec, 'updated_by': request.user})
        if created:
            summary['added'] += 1
            status, msg = 'added', "New tag registered."
        elif tag.gateway != gateway or tag.hardware_spec != spec:
            tag.gateway, tag.hardware_spec, tag.updated_by = gateway, spec, request.user
            tag.save()
            summary['updated'] += 1
            status, msg = 'updated', "Updated metadata."
        else:
            summary['unchanged'] += 1
            status, msg = 'unchanged', "No changes."
        results.append({'mac': sanitized_id, 'status': status, 'message': msg})

    return render(request, 'admin/core/esltag/import_preview.html', {'summary': summary, 'results': results, 'opts': ESLTag._meta})

@login_required
def preview_product_import(request):
    """Multi-step Modisoft product import with preview and confirmation."""
    active_store = getattr(request, 'active_store', None)
    if not active_store:
        messages.error(request, "Select a store first.")
        return redirect('admin:core_product_changelist')

    if request.method == "POST":
        if "confirm_save" in request.POST:
            temp_path = request.POST.get("temp_file_path")
            if not temp_path or not temp_path.startswith(settings.MEDIA_ROOT):
                messages.error(request, "Invalid file.")
                return redirect('admin:core_product_changelist')
            
            results, error = process_modisoft_file_logic(temp_path, active_store, request.user, commit=True)
            if not error:
                os.remove(temp_path)
                messages.success(request, f"Imported {len(results['new'])} new, updated {len(results['update'])} products.")
                return redirect('admin:core_product_changelist')
            messages.error(request, error)
        elif request.FILES.get("import_file"):
            myfile = request.FILES["import_file"]
            filename = default_storage.save(f'tmp/{myfile.name}', myfile)
            temp_path = os.path.join(settings.MEDIA_ROOT, filename)
            results, error = process_modisoft_file_logic(temp_path, active_store, request.user, commit=False)
            if error:
                messages.error(request, error)
                return redirect('admin:core_product_changelist')
            return render(request, "admin/core/product/import_preview.html", {"results": results, "temp_file_path": temp_path, "store": active_store})

    return render(request, "admin/core/product/import_upload.html", {"store": active_store})

@login_required
def bulk_map_tags_view(request):
    """Processes bulk product-to-tag mapping from barcode scanner input."""
    opts = ESLTag._meta
    context = {'opts': opts, 'app_label': opts.app_label, 'title': "Bulk Product-Tag Mapping"}

    if request.method == "POST":
        if 'confirm_mapping' in request.POST:
            proposed_data = request.session.get('pending_bulk_maps', [])
            with transaction.atomic():
                for item in proposed_data:
                    ESLTag.objects.filter(id=item['tag_id']).update(paired_product_id=item['product_id'], updated_by=request.user)
                    # Note: .update() doesn't trigger signals, but we added manual trigger in save()
                    # For bulk updates, we might need a manual trigger here or use a signal.
                    # Since we only have a few, we can iterate and save for now or call the task.
                    from .tasks import update_tag_image_task
                    update_tag_image_task.delay(item['tag_id'])
            messages.success(request, f"Successfully mapped {len(proposed_data)} tags.")
            if 'pending_bulk_maps' in request.session: del request.session['pending_bulk_maps']
            return redirect("admin:core_esltag_changelist")

        import_file = request.FILES.get('import_file')
        if not import_file: return redirect(request.path)

        try:
            raw_text = import_file.read().decode('utf-8')
        except:
            messages.error(request, "Invalid encoding.")
            return redirect(request.path)
        
        active_store = getattr(request, 'active_store', None)
        processor = BulkMapProcessor(raw_text, active_store, request.user)
        proposed, rejections = processor.process()
        request.session['pending_bulk_maps'] = proposed
        context.update({'proposed': proposed, 'rejections': rejections, 'stage': 'preview'})
        return render(request, 'admin/core/esltag/bulk_map_preview.html', context)

    return render(request, 'admin/core/esltag/bulk_map_upload.html', context)
