from django.contrib import admin, messages
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.conf import settings
from django.utils import timezone
from django.http import HttpResponse
import time
import json
import logging
import traceback
import os
from PIL import Image, ImageDraw

from ..models import Company, User, Store, Gateway, Product, ESLTag, TagHardware, Supplier
from ..utils import template_v1, template_v2, template_v3

logger = logging.getLogger(__name__)

class SAISAdminSite(admin.AdminSite):
    """
    Custom admin site with SAIS branding, grouped menu, and Design Lab.
    Provides the central hub for the ESL management platform.
    """
    site_header = "SAIS Platform Administration"
    site_title = "SAIS Admin"
    index_title = "Welcome to SAIS Control Panel"

    def get_urls(self):
        """Register custom admin views for dashboard and template lab."""
        urls = super().get_urls()
        custom_urls = [
            path('template-lab/', self.admin_view(self.template_gallery), name='template-gallery'),
            path('template-render/<int:spec_id>/', self.admin_view(self.mock_render_view), name='template-render'),
            path('dashboard/', self.admin_view(self.dashboard_view), name="dashboard"),
        ]
        return custom_urls + urls

    def dashboard_view(self, request):
        """Renders the analytics dashboard for the active store."""
        try:
            active_store = getattr(request, 'active_store', None)
            if not active_store:
                return redirect('admin:index')

            tags_qs = ESLTag.objects.for_store(active_store)
            gateways_qs = Gateway.objects.for_store(active_store)
            products_qs = Product.objects.for_store(active_store)

            tag_count = tags_qs.count()
            low_battery_count = tags_qs.filter(battery_level__lte=20).count()

            tag_types = list(tags_qs.values('hardware_spec__model_number').annotate(
                count=Count('id')
            ).order_by('-count'))

            for item in tag_types:
                item['percent'] = (item['count'] / tag_count * 100) if tag_count > 0 else 0

            gateway_loads = []
            for gw in gateways_qs:
                count = tags_qs.filter(gateway=gw).count()
                gateway_loads.append({
                    'gateway_mac': gw.gateway_mac,
                    'estation_id': gw.estation_id,
                    'is_active': gw.is_active,
                    'tag_count': count,
                    'load_percent': min(int((count / 500) * 100), 100)
                })

            context = {
                'active_store': active_store,
                'gateway_count': gateways_qs.count(),
                'active_gateways': gateways_qs.filter(is_active=True).count(),
                'tag_count': tag_count,
                'tags_with_products': tags_qs.exclude(paired_product__isnull=True).count(),
                'low_battery_count': low_battery_count,
                'product_count': products_qs.count(),
                'tag_types': tag_types,
                'gateway_loads': gateway_loads,
                'sync_stats': {
                    'success': tags_qs.filter(sync_state='SUCCESS').count(),
                    'pushed': tags_qs.filter(sync_state='PUSHED').count(),
                    'ready': tags_qs.filter(sync_state='IMAGE_READY').count(),
                    'processing': tags_qs.filter(sync_state='PROCESSING').count(),
                    'idle': tags_qs.filter(sync_state='IDLE').count(),
                    'failed_total': tags_qs.filter(Q(sync_state__contains='FAILED') | Q(sync_state='FAILED')).count(),
                    'gen_failed': tags_qs.filter(sync_state='GEN_FAILED').count(),
                    'push_failed': tags_qs.filter(sync_state='PUSH_FAILED').count(),
                }
            }

            context.update(self.each_context(request))
            return render(request, "admin/dashboard.html", context)
        except Exception as e:
            logger.exception("Error in dashboard_view")
            messages.error(request, "Could not load dashboard data.")
            return redirect('admin:index')

    def template_gallery(self, request):
        """Renders the gallery of hardware specs for template testing."""
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
        """Generates a mock preview image using the current template code."""
        try:
            spec = TagHardware.objects.get(pk=spec_id)
            template_id = int(request.GET.get('t', 1))
            is_promo_active = request.GET.get('promo', 'false') == 'true'

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

            image = Image.new('RGB', (width, height), color=(255, 255, 255))
            draw = ImageDraw.Draw(image)

            if template_id == 3:
                template_v3(image, draw, mock_product, width, height, color_scheme)
            elif template_id == 2:
                template_v2(image, draw, mock_product, width, height, color_scheme)
            else:
                template_v1(image, draw, mock_product, width, height, color_scheme)

            response = HttpResponse(content_type="image/png")
            response['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
            image.save(response, "PNG")
            return response

        except Exception as e:
            logger.exception(f"Design Lab Render Error for spec {spec_id}")
            err_img = Image.new('RGB', (400, 150), color='#fee2e2')
            d = ImageDraw.Draw(err_img)
            d.text((10, 10), "PYTHON RENDER ERROR:", fill="black")
            d.text((10, 30), str(e)[:100], fill="red")
            response = HttpResponse(content_type="image/png")
            err_img.save(response, "PNG")
            return response

    def each_context(self, request):
        """Adds custom CSS and dashboard URL to the admin context."""
        context = super().each_context(request)
        context['dashboard_url'] = reverse('sais_admin:dashboard')
        context['custom_admin_css'] = mark_safe("""
            <style>
                :root {
                    --bg-sidebar: #f1f5f9;
                    --navy: #003459;
                    --dark-navy: #00171F;
                    --azure: #C7EFFF;
                    --white: #ffffff;
                    --light-gray: #f1f5f9;
                }
                #header { background: var(--navy); border-bottom: 3px solid var(--light-gray); }
                #branding h1 a { color: var(--white) !important; }
                /* Dashboard Layout Fixes */
                .dashboard-grid {
                    display: grid !important;
                    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)) !important;
                    gap: 1.5rem !important;
                    padding: 1.5rem !important;
                    max-width: 100% !important;
                    box-sizing: border-box !important;
                }
                .stat-card {
                    background: white !important;
                    padding: 1.5rem !important;
                    border-radius: 8px !important;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.05) !important;
                    border: 1px solid #e2e8f0 !important;
                    min-height: 120px !important;
                }
                .stat-card h3 {
                    margin: 0 0 0.5rem 0 !important;
                    font-size: 0.75rem !important;
                    color: #64748b !important;
                    text-transform: uppercase !important;
                    letter-spacing: 0.05em !important;
                }
                .stat-card .value {
                    font-size: 2rem !important;
                    font-weight: 800 !important;
                    color: #1e293b !important;
                    line-height: 1 !important;
                }
                .load-bar-bg { background: #f1f5f9; height: 6px; border-radius: 3px; margin-top: 10px; }
                .load-bar-fill { background: #3b82f6; height: 100%; border-radius: 3px; }
                .tag-list { list-style: none; padding: 0; margin: 10px 0 0 0; font-size: 0.85rem; }
                .tag-list li { display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid #f1f5f9; }


                #nav-sidebar .section {
                    color: var(--white) !important;
                    background: #417690 !important;
                    margin: 0 !important;
                    display: flex;
                    align-items: center;
                    border: none !important;
                }
                .app-inventory .section:before { content: "📦"; margin-right: 10px; }
                .app-hardware .section:before { content: "📡"; margin-right: 10px; }
                .app-organisation .section:before { content: "🏢"; margin-right: 10px; }
                .app-monitoring .section:before { content: "⚙️"; margin-right: 10px; }


                .dashboard-link {
                    background: #2563eb !important;
                    margin: 10px !important;
                    border-radius: 4px;
                    padding: 8px !important;
                    display: block;
                    text-align: center;
                    color: white !important;
                    font-weight: bold;
                    text-transform: uppercase;
                    font-size: 11px;
                    text-decoration: none;
                }
                .dashboard-link:hover { background: #1d4ed8 !important; }

                /* Sidebar Icon for the Design Lab */
                #nav-sidebar .model-design-lab th a:before { content: "🧪 "; }

                #nav-sidebar th a { color: var(--navy) !important; }
                #nav-sidebar .model-esltag th a:before { content: "🏷️ "; }
                #nav-sidebar .model-product th a:before { content: "🛒 "; }
                #nav-sidebar .model-supplier th a:before { content: "🏭 "; }
                #nav-sidebar .model-gateway th a:before { content: "📟 "; }
                #nav-sidebar .model-taghardware th a:before { content: "🛠️ "; }
                #nav-sidebar .model-company th a:before { content: "🏭 "; }
                #nav-sidebar .model-store th a:before { content: "🏪 "; }
                #nav-sidebar .model-user th a:before { content: "👤 "; }
                #nav-sidebar .model-group th a:before { content: "👥 "; }
                #nav-sidebar .model-taskresult th a:before { content: "📊 "; }
                #nav-sidebar .model-groupresult th a:before { content: "📁 "; }

                #nav-sidebar .addlink {
                    background: var(--navy) !important;
                    color: var(--white) !important;
                    padding: 3px 8px !important;
                    border-radius: 4px;
                    text-transform: uppercase;
                    font-size: 10px;
                    background-image: none !important;
                }
                #nav-sidebar .addlink:after { content: "" !important; }
                .object-tools a {
                    background-color: #003459 !important;
                    color: #ffffff !important;
                    border-radius: 50px !important;
                    padding: 6px 15px !important;
                    text-transform: uppercase;
                    font-size: 11px;
                    font-weight: bold;
                }
                .object-tools a:hover { background-color: #00A8E8 !important; }
                .field-sync_button a.button, a.button {
                    background: var(--azure) !important;
                    color: var(--dark-navy) !important;
                    border-radius: 4px !important;
                    padding: 4px 12px !important;
                }
                #nav-sidebar tr.current-model { background: var(--azure) !important; }
                #nav-sidebar tr.current-model th a { color: var(--dark-navy) !important; }

                #nav-sidebar .model-design-lab .addlink,
                #nav-sidebar .model-taskresult .addlink,
                #nav-sidebar .model-groupresult .addlink { display: none !important; }
                .app-django_celery_results .object-tools { display: none !important; }
                .sync-success { color: #059669; font-weight: bold; }
                .sync-pending { color: #ea580c; font-style: italic; }
            </style>
        """)
        return context

    def get_app_list(self, request, app_label=None):
        """Customizes the admin sidebar by grouping models into logical sections."""
        try:
            app_dict = self._build_app_dict(request)
            if not app_dict: return []

            all_models = []
            for app in app_dict.values():
                all_models.extend(app['models'])

            def find_model(name): return next((m for m in all_models if m['object_name'].lower() == name.lower()), None)

            inventory = {'name': 'Inventory', 'models': [m for m in [find_model('ESLTag'), find_model('Product'), find_model('Supplier')] if m]}
            hardware = {'name': 'Hardware', 'models': [m for m in [find_model('Gateway'), find_model('TagHardware')] if m]}
            org = {'name': 'Organisation', 'models': [m for m in [find_model('Company'), find_model('Store'), find_model('User'), find_model('Group')] if m]}

            monitoring = {'name': 'System Monitoring', 'models': []}
            monitoring['models'].append({
                'name': '📊 Analytics Dashboard',
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

                celery_res = find_model('TaskResult')
                if celery_res: monitoring['models'].append(celery_res)

            groups = [inventory, hardware, org]
            if monitoring['models']: groups.append(monitoring)

            return groups
        except Exception:
            logger.exception("Error in get_app_list")
            return super().get_app_list(request, app_label)

admin_site = SAISAdminSite(name='sais_admin')

# --- MIXINS ---

class AuditAdminMixin:
    """
    Automatically stamps the user who last modified the record
    and assigns the active store for new records if applicable.
    """
    def save_model(self, request, obj, form, change):
        try:
            # Update modified by user
            if hasattr(obj, 'updated_by_id') or any(f.name == 'updated_by' for f in obj._meta.fields):
                obj.updated_by = request.user

            # Automatically assign active store for new objects if the model has a store field
            if not change and hasattr(request, 'active_store') and request.active_store:
                if any(f.name == 'store' for f in obj._meta.fields):
                    if not getattr(obj, 'store_id', None):
                        obj.store = request.active_store

            super().save_model(request, obj, form, change)
        except Exception as e:
            logger.exception("Error in AuditAdminMixin.save_model")
            raise e

class CompanySecurityMixin(AuditAdminMixin):
    """Restricts access to data based on the user's company and role."""
    def get_queryset(self, request):
        try:
            qs = super().get_queryset(request)
            if request.user.is_superuser:
                return qs

            if hasattr(self.model, 'company'):
                qs = qs.filter(company=request.user.company)
            elif hasattr(self.model, 'store'):
                qs = qs.filter(store__company=request.user.company)
            elif self.model == ESLTag:
                qs = qs.filter(gateway__store__company=request.user.company)

            if request.user.role == 'manager':
                assigned_stores = request.user.managed_stores.all()
                if hasattr(self.model, 'store'):
                    qs = qs.filter(store__in=assigned_stores)
                elif self.model == Store:
                    qs = qs.filter(id__in=assigned_stores.values_list('id', flat=True))
                elif self.model == ESLTag:
                    qs = qs.filter(gateway__store__in=assigned_stores)
            return qs
        except Exception:
            logger.exception("Error in CompanySecurityMixin.get_queryset")
            return self.model.objects.none()

class UIHelperMixin:
    """Utility methods for common UI components in the admin."""
    def sync_button(self, obj):
        try:
            url = reverse('admin:sync-tag-manual', args=[obj.pk])
            return format_html('<a class="button" href="{}" style="background:#2563eb; color:white;">Sync</a>', url)
        except:
            return ""
    sync_button.short_description = "Action"
