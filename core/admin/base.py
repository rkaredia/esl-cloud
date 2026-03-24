from django.contrib import admin, messages
from django.urls import path, reverse, NoReverseMatch
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.conf import settings
from django.utils import timezone
from django.http import HttpResponse
from django import forms
from django.db import models
import time
import json
import logging
import traceback
import os
from PIL import Image, ImageDraw

from ..models import Company, User, Store, Gateway, Product, ESLTag, TagHardware, Supplier, GlobalSetting, MQTTMessage
from ..utils import template_v1, template_v2, template_v3

"""
CUSTOM DJANGO ADMIN ARCHITECTURE
--------------------------------
The Django Admin is a powerful, automatic interface for managing data.
In this project, we have subclassed the standard 'AdminSite' to create
the 'SAIS Control Panel'.

Key Customizations:
1. CUSTOM URLS: We added views for the Dashboard and the Design Lab.
2. CONTEXT INJECTION: We inject custom CSS/JS into every page to change
   the look and feel (e.g., adding the store selector).
3. MODEL GROUPING: We reorganized the sidebar into logical business
   sections (Inventory, Hardware, Organisation).
4. MULTI-TENANT SECURITY: Mixins ensure users only see data for their
   authorized companies and stores.

Think of this as the 'Front-End' for the internal data warehouse and
IOT management system.
"""

logger = logging.getLogger(__name__)

class SAISAdminSite(admin.AdminSite):
    """
    SAIS CONTROL PANEL
    ------------------
    The central hub for the platform. This class overrides the default
    Django Admin behavior to provide a custom, branded experience.
    """
    site_header = "SAIS Platform Administration"
    site_title = "SAIS Admin"
    index_title = "Welcome to SAIS Control Panel"

    def get_urls(self):
        """
        URL ROUTING OVERRIDE
        --------------------
        EDUCATIONAL: Django matches URLs against a list. By overriding
        get_urls(), we can add our own 'Pages' to the Admin.
        """
        urls = super().get_urls()
        custom_urls = [
            # Template Design Lab: A playground to preview ESL image layouts
            path('template-lab/', self.admin_view(self.template_gallery), name='template-gallery'),
            path('template-render/<int:spec_id>/', self.admin_view(self.mock_render_view), name='template-render'),

            # Analytics Dashboard: The landing page showing store stats
            path('dashboard/', self.admin_view(self.dashboard_view), name="dashboard"),
        ]
        return custom_urls + urls

    def index(self, request, extra_context=None):
        """
        LANDING PAGE OVERRIDE
        ---------------------
        Instead of showing the standard app list, we send users straight
        to the Analytics Dashboard.
        """
        return redirect('sais_admin:dashboard')

    def dashboard_view(self, request):
        """
        ANALYTICS DASHBOARD LOGIC
        -------------------------
        This view acts as a 'Data Aggregator'. It queries the DB for
        various counts and statuses (Battery levels, Sync states,
        Gateway loads) and passes them to the 'dashboard.html' template.
        """
        try:
            active_store = getattr(request, 'active_store', None)
            if not active_store:
                return redirect('admin:index')

            # Aggregate data for the active store only
            tags_qs = ESLTag.objects.for_store(active_store)
            gateways_qs = Gateway.objects.for_store(active_store)
            products_qs = Product.objects.for_store(active_store)

            # PERFORMANCE: Combine multiple count queries into a single aggregate call for ESLTag
            # This reduces database round-trips from ~12 to 1 for tag-related stats.
            tag_stats = tags_qs.aggregate(
                total=Count('id'),
                low_battery=Count('id', filter=Q(battery_level__lte=20)),
                with_products=Count('id', filter=Q(paired_product__isnull=False)),
                success=Count('id', filter=Q(sync_state='SUCCESS')),
                pushed=Count('id', filter=Q(sync_state='PUSHED')),
                ready=Count('id', filter=Q(sync_state='IMAGE_READY')),
                processing=Count('id', filter=Q(sync_state='PROCESSING')),
                idle=Count('id', filter=Q(sync_state='IDLE')),
                failed_total=Count('id', filter=Q(Q(sync_state__contains='FAILED') | Q(sync_state='FAILED'))),
                gen_failed=Count('id', filter=Q(sync_state='GEN_FAILED')),
                push_failed=Count('id', filter=Q(sync_state='PUSH_FAILED')),
            )
            tag_count = tag_stats['total']

            # PERFORMANCE: Combine gateway counts into one query
            gateway_stats = gateways_qs.aggregate(
                total=Count('id'),
                online=Count('id', filter=Q(is_online=True))
            )

            # GROUP BY: Count how many of each hardware model exists
            tag_types = list(tags_qs.values('hardware_spec__model_number').annotate(
                count=Count('id')
            ).order_by('-count'))

            for item in tag_types:
                item['percent'] = (item['count'] / tag_count * 100) if tag_count > 0 else 0

            # PERFORMANCE: Use annotate(Count) to avoid N+1 queries when calculating gateway loads.
            gateway_loads = []
            for gw in gateways_qs.annotate(tag_count_ann=Count('tags')):
                count = gw.tag_count_ann
                gateway_loads.append({
                    'gateway_mac': gw.gateway_mac,
                    'estation_id': gw.estation_id,
                    'is_active': gw.is_online,
                    'tag_count': count,
                    'load_percent': min(int((count / 500) * 100), 100) # Max 500 tags per gateway
                })

            context = {
                'active_store': active_store,
                'gateway_count': gateway_stats['total'],
                'active_gateways': gateway_stats['online'],
                'tag_count': tag_count,
                'tags_with_products': tag_stats['with_products'],
                'low_battery_count': tag_stats['low_battery'],
                'product_count': products_qs.count(),
                'tag_types': tag_types,
                'gateway_loads': gateway_loads,
                'sync_stats': {
                    'success': tag_stats['success'],
                    'pushed': tag_stats['pushed'],
                    'ready': tag_stats['ready'],
                    'processing': tag_stats['processing'],
                    'idle': tag_stats['idle'],
                    'failed_total': tag_stats['failed_total'],
                    'gen_failed': tag_stats['gen_failed'],
                    'push_failed': tag_stats['push_failed'],
                }
            }

            # each_context() adds standard admin variables (user, site_header, etc.)
            context.update(self.each_context(request))
            return render(request, "admin/dashboard.html", context)
        except Exception as e:
            logger.exception("Error in dashboard_view")
            messages.error(request, "Could not load dashboard data.")
            return redirect('admin:index')

    def template_gallery(self, request):
        """Displays all supported hardware models for testing."""
        try:
            specs = TagHardware.objects.all()
            return render(request, 'admin/core/template_gallery.html', {
                'specs': specs,
                'title': 'ESL Template Design Lab',
                **self.each_context(request),
            })
        except Exception:
            logger.exception("Error in template_gallery")
            return redirect('admin:index')

    def mock_render_view(self, request, spec_id):
        """
        THE MOCK RENDERER
        -----------------
        Generates a preview PNG image on-the-fly without needing a real
        tag or database record. This allows developers to tweak layouts
        and see results instantly.
        """
        try:
            spec = TagHardware.objects.get(pk=spec_id)
            template_id = int(request.GET.get('t', 1))
            is_promo_active = request.GET.get('promo', 'false') == 'true'

            # 'Mock' objects simulate a Product so we can use the real template functions
            class MockSupplier:
                def __init__(self, abbreviation):
                    self.abbreviation = abbreviation

            class MockProduct:
                def __init__(self, name, price, sku, supplier_abbr="GSC", is_on_special=False):
                    self.name = name
                    self.price = price
                    self.sku = sku
                    self.is_on_special = is_on_special
                    self.preferred_supplier = MockSupplier(supplier_abbr)

            mock_product = MockProduct(
                name="PREVIEW PRODUCT NAME LONG DESCRIPTION",
                price="88.50" if is_promo_active else "124.50",
                sku="123456789000",
                is_on_special=is_promo_active
            )

            width = int(spec.width_px) if spec.width_px else 296
            height = int(spec.height_px) if spec.height_px else 128
            color_scheme = (spec.color_scheme or "BW").upper()

            # Create a blank white canvas
            image = Image.new('RGB', (width, height), color=(255, 255, 255))
            draw = ImageDraw.Draw(image)

            # Route to the physical layout logic in utils.py
            if template_id == 3:
                template_v3(image, draw, mock_product, width, height, color_scheme)
            elif template_id == 2:
                template_v2(image, draw, mock_product, width, height, color_scheme)
            else:
                template_v1(image, draw, mock_product, width, height, color_scheme)

            # Return the image directly to the browser as a PNG
            response = HttpResponse(content_type="image/png")
            response['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
            image.save(response, "PNG")
            return response

        except Exception as e:
            logger.exception(f"Design Lab Render Error for spec {spec_id}")
            # Render a 'Red Box' with the error message if something fails
            err_img = Image.new('RGB', (400, 150), color='#fee2e2')
            d = ImageDraw.Draw(err_img)
            d.text((10, 10), "PYTHON RENDER ERROR:", fill="black")
            d.text((10, 30), str(e)[:100], fill="red")
            response = HttpResponse(content_type="image/png")
            err_img.save(response, "PNG")
            return response

    def each_context(self, request):
        """
        GLOBAL CONTEXT INJECTION
        ------------------------
        Variables added here are available in EVERY admin page.
        We use this to inject our custom CSS and JS themes.
        """
        context = super().each_context(request)
        context['dashboard_url'] = reverse('sais_admin:dashboard')
        context['is_nav_sidebar_enabled'] = True
        context['available_apps'] = self.get_app_list(request)

        # Inject custom styles and scripts using 'format_html' for security
        context['custom_admin_css'] = format_html('<link rel="stylesheet" type="text/css" href="{}{}admin/css/sais_admin.css">', settings.STATIC_URL, "")
        context['custom_admin_js'] = format_html('<script src="{}{}admin/js/sais_admin.js" defer></script>', settings.STATIC_URL, "")
        return context

    def get_app_list(self, request, app_label=None):
        """
        SIDEBAR REORGANIZATION
        ----------------------
        By default, Django groups models by 'Application' (folder).
        This method overrides that to group them by 'Business Function'.
        """
        try:
            # Get the raw list of all models registered in the admin
            app_dict = self._build_app_dict(request)
            if not app_dict: return []

            # Flatten the list so we can pick and choose where they go
            all_models = []
            for app in app_dict.values():
                all_models.extend(app['models'])

            def find_model(name): return next((m for m in all_models if m['object_name'].lower() == name.lower()), None)

            # --- DEFINE CUSTOM GROUPS ---

            # Group 1: Daily Store Operations
            inventory_models = []
            m = find_model('ESLTag')
            if m: m['name'] = 'ESL Tags'; inventory_models.append(m)
            m = find_model('Product')
            if m: m['name'] = 'Products'; inventory_models.append(m)
            m = find_model('Supplier')
            if m: m['name'] = 'Suppliers'; inventory_models.append(m)

            inventory = {
                'name': '📦 INVENTORY',
                'app_label': 'inventory',
                'models': inventory_models
            }

            # Group 2: Base Stations & Hardware setup
            hardware_models = []
            m = find_model('Gateway')
            if m: m['name'] = 'Gateways'; hardware_models.append(m)
            m = find_model('TagHardware')
            if m: m['name'] = 'Tag hardwares'; hardware_models.append(m)
            m = find_model('GlobalSetting')
            if m: m['name'] = 'Global Settings'; hardware_models.append(m)

            hardware = {
                'name': '📡 HARDWARE',
                'app_label': 'hardware',
                'models': hardware_models
            }

            # Group 3: Multi-tenant management (Admin only)
            org_models = []
            m = find_model('Company')
            if m: m['name'] = 'Companies'; org_models.append(m)
            m = find_model('Store')
            if m: m['name'] = 'Stores'; org_models.append(m)
            m = find_model('User')
            if m: m['name'] = 'Users'; org_models.append(m)
            m = find_model('Group')
            if m: m['name'] = 'Groups'; org_models.append(m)

            org = {
                'name': '🏢 ORGANISATION',
                'app_label': 'organisation',
                'models': org_models
            }

            # Group 4: Logs & Monitoring
            monitoring = {
                'name': '⚙️ SYSTEM MONITORING',
                'app_label': 'monitoring',
                'models': []
            }

            # Add 'Virtual' models (links to our custom views) to the sidebar
            monitoring['models'].append({
                'name': 'Analytics Dashboard',
                'object_name': 'dashboard',
                'admin_url': reverse('sais_admin:dashboard'),
                'view_only': True,
            })

            if request.user.is_superuser or request.user.role in ['owner', 'manager']:
                monitoring['models'].append({
                    'name': 'Template Design Lab',
                    'object_name': 'design-lab',
                    'admin_url': reverse('sais_admin:template-gallery'),
                    'view_only': True,
                })

                # Background Task logs
                celery_res = find_model('TaskResult')
                if celery_res:
                    celery_res['name'] = 'Task results'
                    monitoring['models'].append(celery_res)

                # Raw MQTT Packet logs
                mqtt_logs = find_model('MQTTMessage')
                if mqtt_logs:
                    mqtt_logs['name'] = 'eStation Communication'
                    monitoring['models'].append(mqtt_logs)

            groups = [inventory, hardware, org]
            if monitoring['models']: groups.append(monitoring)

            return groups
        except Exception:
            logger.exception("Error in get_app_list")
            return super().get_app_list(request, app_label)

# EXPORT: This instance replaces the default django.contrib.admin.site
admin_site = SAISAdminSite(name='sais_admin')

# =================================================================
# GLOBAL SYSTEM SETTINGS ADMIN
# =================================================================

@admin.register(GlobalSetting, site=admin_site)
class GlobalSettingAdmin(admin.ModelAdmin):
    """
    SYSTEM CONFIGURATION UI
    -----------------------
    Restricted to Superusers only. Manages things like 'LOG_RETENTION_DAYS'.
    """
    list_display = ('key', 'value_display', 'description')
    search_fields = ('key', 'description')

    def value_display(self, obj):
        """Prettifies the value field in the list view with a 'Code' look."""
        val = obj.value
        if len(val) > 100:
            val = val[:97] + "..."
        return format_html('<code style="background: #f1f5f9; color: #0f172a; padding: 2px 6px; border-radius: 4px; font-family: monospace; font-size: 0.9em;">{}</code>', val)
    value_display.short_description = "Value"

    # UI Enhancement: Use a monospace font for the text area
    formfield_overrides = {
        models.TextField: {'widget': forms.Textarea(attrs={'rows': 2, 'cols': 40, 'style': 'font-family: monospace; width: 100%; max-width: 600px; padding: 8px; border: 1px solid #ccc; border-radius: 4px;'})},
    }

    # PERMISSIONS: Only Superusers can see/touch global settings
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

# =================================================================
# SECURITY & AUDIT MIXINS
# =================================================================

class AuditAdminMixin:
    """
    AUTOMATIC AUDIT LOGGING
    ----------------------
    EDUCATIONAL: 'save_model' is called when you click 'Save' in the admin.
    This mixin automatically sets the 'updated_by' field to the current user.
    """
    def save_model(self, request, obj, form, change):
        try:
            # 1. Update the 'Who' audit field
            if hasattr(obj, 'updated_by_id') or any(f.name == 'updated_by' for f in obj._meta.fields):
                obj.updated_by = request.user

            # 2. Store Inheritance: For NEW objects, automatically assign them to
            # the store currently selected in the user's session.
            if not change and hasattr(request, 'active_store') and request.active_store:
                if any(f.name == 'store' for f in obj._meta.fields):
                    if not getattr(obj, 'store_id', None):
                        obj.store = request.active_store

            super().save_model(request, obj, form, change)
        except Exception as e:
            logger.exception("Error in AuditAdminMixin.save_model")
            raise e

class CompanySecurityMixin(AuditAdminMixin):
    """
    HIERARCHICAL SECURITY MIXIN
    ---------------------------
    Filters all data based on the User's Company and Assigned Stores.
    This is the primary defense against IDOR (Insecure Direct Object Reference)
    within the Admin interface.
    """
    def get_queryset(self, request):
        try:
            qs = super().get_queryset(request)

            # Superusers bypass all filters
            if request.user.is_superuser:
                return qs

            # LEVEL 1: COMPANY ISOLATION
            # If the model has a company link, only show records for the user's company.
            if hasattr(self.model, 'company'):
                qs = qs.filter(company=request.user.company)
            elif hasattr(self.model, 'store'):
                qs = qs.filter(store__company=request.user.company)
            elif self.model == ESLTag:
                qs = qs.filter(gateway__store__company=request.user.company)
            elif self.model.__name__ == 'MQTTMessage':
                from ..models import Gateway
                authorized_gateway_ids = Gateway.objects.filter(store__company=request.user.company).values_list('estation_id', flat=True)
                qs = qs.filter(estation_id__in=authorized_gateway_ids)

            # LEVEL 2: STORE ISOLATION (For Managers)
            # If the user is a manager, only show data for their specific stores.
            if request.user.role == 'manager':
                assigned_stores = request.user.managed_stores.all()
                if hasattr(self.model, 'store'):
                    qs = qs.filter(store__in=assigned_stores)
                elif self.model == Store:
                    qs = qs.filter(id__in=assigned_stores.values_list('id', flat=True))
                elif self.model == ESLTag:
                    qs = qs.filter(gateway__store__in=assigned_stores)
                elif self.model.__name__ == 'MQTTMessage':
                    from ..models import Gateway
                    authorized_gateway_ids = Gateway.objects.filter(store__in=assigned_stores).values_list('estation_id', flat=True)
                    qs = qs.filter(estation_id__in=authorized_gateway_ids)
            return qs
        except Exception:
            logger.exception("Error in CompanySecurityMixin.get_queryset")
            # Fail Securely: Return nothing if an error occurs
            return self.model.objects.none()

class UIHelperMixin:
    """
    REUSABLE UI COMPONENTS
    ----------------------
    Contains methods that generate HTML 'Snippets' for the admin list view.
    """
    def sync_button(self, obj):
        """Generates a Navy 'Sync' button to manually trigger a tag refresh."""
        try:
            try:
                url = reverse('sais_admin:sync-tag-manual', args=[obj.pk])
            except NoReverseMatch:
                url = reverse('admin:sync-tag-manual', args=[obj.pk])
            return format_html('<a class="btn-sync" href="{}" title="Manually trigger tag update" aria-label="Sync tag">Sync</a>', url)
        except NoReverseMatch:
            return ""
    sync_button.short_description = "Action"
