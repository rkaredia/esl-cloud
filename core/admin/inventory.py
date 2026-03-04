from django.contrib import admin, messages
from django.urls import path, reverse
from django.utils.safestring import mark_safe
from django.db.models import Count, Q
from .base import admin_site, CompanySecurityMixin, UIHelperMixin
from .mixins import StoreFilteredAdmin
from ..models import Product, Supplier, ESLTag
from ..views import preview_product_import

@admin.register(Supplier, site=admin_site)
class SupplierAdmin(admin.ModelAdmin):
    """Admin for Managing Suppliers."""
    list_display = ('name', 'abbreviation')
    search_fields = ('name', 'abbreviation')
    ordering = ('name',)

@admin.register(Product, site=admin_site)
class ProductAdmin(CompanySecurityMixin, UIHelperMixin, StoreFilteredAdmin):
    """Admin for Managing Products and their pricing."""
    list_display = ('image_status', 'sku', 'name', 'price', 'store', 'sync_button', 'preferred_supplier', 'created_at', 'updated_at', 'updated_by')
    list_editable = ('name', 'price', 'preferred_supplier')
    search_fields = ('sku', 'name')
    readonly_fields = ('updated_at', 'updated_by', 'image_status', 'store', 'created_at')
    change_list_template = "admin/core/product/change_list.html"

    fieldsets = (
        ('General Info', {'fields': ('sku', 'name', 'preferred_supplier', 'image_status', 'store')}),
        ('Pricing', {'fields': ('price', 'is_on_special')}),
        ('Audit', {'fields': ('created_at', 'updated_at', 'updated_by')}),
    )

    list_max_show_all = 100
    show_full_result_count = False

    actions = ['safe_delete', 'regenerate_product_images', 'refresh_all_store_images']

    @admin.action(description="Delete selected (Max 100)")
    def safe_delete(self, request, queryset):
        if queryset.count() > 100:
            self.message_user(request, "Error: Max 100 items allowed.", messages.ERROR)
            return
        queryset.delete()

    @admin.action(description="Regenerate Tag Images for selected products")
    def regenerate_product_images(self, request, queryset):
        from ..utils import trigger_bulk_sync
        if queryset.count() > 100:
            self.message_user(request, "Error: Please select maximum 100 items.", messages.ERROR)
            return
        tag_ids = list(ESLTag.objects.filter(paired_product__in=queryset).values_list('id', flat=True))
        if tag_ids:
            trigger_bulk_sync(tag_ids)
            self.message_user(request, f"Queued {len(tag_ids)} tag updates.")
        else:
            self.message_user(request, "No tags found for selected products.", messages.WARNING)

    @admin.action(description="Refresh ALL images for this Store")
    def refresh_all_store_images(self, request, queryset):
        if not request.active_store:
            self.message_user(request, "Please select a store first.", messages.WARNING)
            return
        from ..tasks import refresh_store_products_task
        refresh_store_products_task.delay(request.active_store.id)
        self.message_user(request, f"Task started: Refreshing all products for {request.active_store.name}")

    def image_status(self, obj):
        has_image = obj.esl_tags.filter(tag_image__gt='').exists()
        has_tag = obj.esl_tags.exists()
        if has_image: return mark_safe('<span style="color: #059669; font-weight: bold;">● Generated</span>')
        if has_tag: return mark_safe('<span style="color: #ea580c; font-weight: bold;">● Pending</span>')
        return mark_safe('<span style="color: #94a3b8;">○ No Tag</span>')
    image_status.short_description = "Status"
    image_status.admin_order_field = 'has_tag_image'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(has_tag_image=Count('esl_tags', filter=Q(esl_tags__tag_image__gt='')))

    def get_urls(self):
        return [path('import-modisoft/', self.admin_site.admin_view(preview_product_import), name='import-modisoft')] + super().get_urls()

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions: del actions['delete_selected']
        return actions
