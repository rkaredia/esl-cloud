from django.contrib import admin, messages
from django.urls import path, reverse
from django.utils.safestring import mark_safe
from django.db.models import Count, Q
from .base import admin_site, CompanySecurityMixin, UIHelperMixin
from .mixins import StoreFilteredAdmin
from ..models import Product, Supplier, ESLTag
from ..views import preview_product_import
import logging

"""
INVENTORY MANAGEMENT ADMIN
--------------------------
This module handles the 'Daily Operations' data: Products and Suppliers.
It features custom 'Bulk Actions' for deleting records or triggering
mass hardware updates.

Key Concepts:
- LIST EDITABLE: Users can change prices directly in the table without
  clicking into each record.
- CUSTOM ACTIONS: Special buttons in the 'Action' dropdown (e.g., 'Safe Delete').
- AUDIT TRAIL: Uses AuditAdminMixin to track who changed what.
"""

logger = logging.getLogger(__name__)

@admin.register(Supplier, site=admin_site)
class SupplierAdmin(admin.ModelAdmin):
    """
    Supplier Registry.
    A simple table with basic search and sort functionality.
    """
    list_display = ('name', 'abbreviation')
    search_fields = ('name', 'abbreviation')
    ordering = ('name',)

@admin.register(Product, site=admin_site)
class ProductAdmin(CompanySecurityMixin, UIHelperMixin, StoreFilteredAdmin):
    """
    THE PRODUCT CATALOG UI
    ----------------------
    Combines security, audit tracking, and store-level filtering.
    """
    # Columns shown in the main table
    list_display = (
        'image_status', 'sku', 'name', 'price', 'store',
        'sync_button', 'preferred_supplier', 'created_at',
        'updated_at', 'updated_by'
    )

    # Allow editing these fields directly in the list view
    list_editable = ('name', 'price', 'preferred_supplier')

    # Search functionality
    search_fields = ('sku', 'name')

    # Non-editable fields for security/audit integrity
    readonly_fields = ('updated_at', 'updated_by', 'image_status', 'store', 'created_at')

    # Use a custom HTML template for the list view to add the 'Import' button
    change_list_template = "admin/core/product/change_list.html"

    # Layout of the individual Edit page
    fieldsets = (
        ('General Info', {'fields': ('sku', 'name', 'preferred_supplier', 'image_status', 'store')}),
        ('Pricing', {'fields': ('price', 'is_on_special')}),
        ('Audit', {'fields': ('created_at', 'updated_at', 'updated_by')}),
    )

    # Performance Tuning: Only show 100 items per page
    list_max_show_all = 100
    show_full_result_count = False

    # REGISTER BULK ACTIONS (Appears in the dropdown above the table)
    actions = ['safe_delete', 'regenerate_product_images', 'refresh_all_store_images']

    @admin.action(description="Delete selected (Max 100)")
    def safe_delete(self, request, queryset):
        """
        Custom delete that prevents accidental deletion of thousands
        of items at once.
        """
        try:
            count = queryset.count()
            if count > 100:
                self.message_user(request, "Error: Max 100 items allowed for bulk deletion.", messages.ERROR)
                return
            queryset.delete()
            self.message_user(request, f"Successfully deleted {count} items.")
        except Exception as e:
            logger.exception("Error in safe_delete action")
            self.message_user(request, "A technical error occurred during deletion.", messages.ERROR)

    @admin.action(description="Regenerate Tag Images for selected products")
    def regenerate_product_images(self, request, queryset):
        """
        Triggers a refresh for every ESL Tag linked to the selected products.
        """
        try:
            from ..utils import trigger_bulk_sync
            count = queryset.count()
            if count > 100:
                self.message_user(request, "Error: Please select maximum 100 items.", messages.ERROR)
                return

            # Find all tag IDs linked to these products
            tag_ids = list(ESLTag.objects.filter(paired_product__in=queryset).values_list('id', flat=True))

            if tag_ids:
                trigger_bulk_sync(tag_ids) # Dispatch to Celery
                self.message_user(request, f"Queued {len(tag_ids)} tag updates across {count} products.")
            else:
                self.message_user(request, "No paired tags found for selected products.", messages.WARNING)
        except Exception as e:
            logger.exception("Error in regenerate_product_images action")
            self.message_user(request, "Failed to queue image regeneration.", messages.ERROR)

    @admin.action(description="Refresh ALL images for this Store")
    def refresh_all_store_images(self, request, queryset):
        """
        A 'Panic/Maintenance' button to refresh the entire store.
        """
        try:
            if not request.active_store:
                self.message_user(request, "Please select a store first.", messages.WARNING)
                return
            from ..tasks import refresh_store_products_task
            refresh_store_products_task.delay(request.active_store.id)
            self.message_user(request, f"Task started: Refreshing all products for {request.active_store.name}")
        except Exception as e:
            logger.exception("Error in refresh_all_store_images action")
            self.message_user(request, "Could not start store-wide refresh.", messages.ERROR)

    def image_status(self, obj):
        """
        UI COMPONENT: Visual badge showing if a tag is ready.
        ---------------------------------------------------
        Returns raw HTML formatted with styles.
        """
        try:
            has_image = obj.esl_tags.filter(tag_image__gt='').exists()
            has_tag = obj.esl_tags.exists()
            if has_image: return mark_safe('<span style="color: #059669; font-weight: bold;">● Generated</span>')
            if has_tag: return mark_safe('<span style="color: #ea580c; font-weight: bold;">● Pending</span>')
            return mark_safe('<span style="color: #94a3b8;">○ No Tag</span>')
        except:
            return "Error"
    image_status.short_description = "Status"
    image_status.admin_order_field = 'has_tag_image'

    def get_queryset(self, request):
        """Pre-calculate (annotate) image counts to improve performance."""
        qs = super().get_queryset(request)
        return qs.annotate(has_tag_image=Count('esl_tags', filter=Q(esl_tags__tag_image__gt='')))

    def get_urls(self):
        """Register the custom Modisoft Excel Import view."""
        return [path('import-modisoft/', self.admin_site.admin_view(preview_product_import), name='import-modisoft')] + super().get_urls()

    def get_actions(self, request):
        """Removes the standard 'delete_selected' to force use of our 'safe_delete'."""
        actions = super().get_actions(request)
        if 'delete_selected' in actions: del actions['delete_selected']
        return actions
