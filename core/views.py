    # core/views.py
import openpyxl
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from .models import Store
from django.contrib import messages
from django.shortcuts import redirect, get_object_or_404
from django.core.exceptions import PermissionDenied
# core/views.py
from django.http import HttpResponse
from openpyxl import Workbook

from .models import ESLTag, Gateway

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
#def set_active_store(request, store_id):
#    # Security: Ensure the store belongs to the user's company
#    store = Store.objects.filter(id=store_id, company=request.user.company).first()
#    if store:
#        request.session['active_store_id'] = store.id
#        request.session.modified = True  # Force session to save
#        messages.success(request, f"Switched to store: {store.name}")
#    else:
#        messages.error(request, "Invalid store selection.")
#        
#    return redirect('/admin/') # Send them back to the main dashboard


   

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
    
    # Headers
    headers = ['tag_mac', 'gateway_mac', 'model_name', 'color_type', 'width_px', 'height_px', 'display_type']
    ws.append(headers)
    
    # Example Row with a MAC address
    ws.append(['00:1A:2B:3C:4D:5E', 'FF:EE:DD:CC:BB:AA', 'GooDisplay 2.1', 'BWR', 250, 122, 'E-Ink'])  

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
        
        # Added 'unchanged' to the summary
        summary = {'added': 0, 'updated': 0, 'rejected': 0, 'unchanged': 0}
        results = []

        for row in sheet.iter_rows(min_row=2, values_only=True):
            tag_mac, gw_mac, model, color, width, height, disp_type = row
            
            # 1. Validation
            gateway = Gateway.objects.filter(gateway_mac=gw_mac, store=active_store).first()

            if not gateway:
                summary['rejected'] += 1
                results.append({
                    'mac': tag_mac, 'status': 'rejected', 
                    'message': f"Gateway MAC {gw_mac} not found in this store."
                })
                continue

            # 2. Logic: Fetch existing or initialize new
            tag = ESLTag.objects.filter(tag_mac=tag_mac).first()
            
            if tag:
                # MENTOR TIP: Compare values before saving
                has_changed = (
                    tag.gateway != gateway or
                    tag.model_name != str(model) or
                    tag.color_type != str(color) or
                    tag.width_px != width or
                    tag.height_px != height or
                    tag.display_type != str(disp_type)
                )

                if has_changed:
                    tag.gateway = gateway
                    tag.model_name = model
                    tag.color_type = color
                    tag.width_px = width
                    tag.height_px = height
                    tag.display_type = disp_type
                    tag.save()
                    summary['updated'] += 1
                    status = 'updated'
                    msg = "Updated metadata"
                else:
                    summary['unchanged'] += 1
                    status = 'unchanged'
                    msg = "No changes detected"
            else:
                # Create New
                ESLTag.objects.create(
                    tag_mac=tag_mac,
                    gateway=gateway,
                    model_name=model,
                    color_type=color,
                    width_px=width,
                    height_px=height,
                    display_type=disp_type
                )
                summary['added'] += 1
                status = 'added'
                msg = "New tag added"
                
            results.append({'mac': tag_mac, 'status': status, 'message': msg})

        return render(request, 'admin/core/esltag/import_preview.html', {
            'summary': summary,
            'results': results,
            'opts': ESLTag._meta,
        })

    return redirect('admin:core_esltag_changelist')