from django.db import models
from django.contrib.auth.models import AbstractUser
import os
from django.utils.text import slugify
from django.conf import settings
from .storage import OverwriteStorage
from django.core.exceptions import ValidationError
from .managers import StoreManager

# =================================================================
# 1. BASE CLASSES & UTILS
# =================================================================

class AuditModel(models.Model):
    """
    Consolidated Base Class for all models.
    Provides creation/update timestamps and user tracking.
    """
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name="%(class)s_updates"
    )

    class Meta:
        abstract = True

def get_tag_path(instance, filename):
    """Generates the storage path for ESL tag images based on company and store."""
    try:
        # Prioritize instance.store, fallback to gateway.store
        store = instance.store
        if not store and instance.gateway:
            store = instance.gateway.store

        company_name = slugify(store.company.name)
        store_name = slugify(store.name)
        ext = os.path.splitext(filename)[1]
        new_filename = f"{instance.tag_mac.replace(':', '')}{ext}"
        return os.path.join(company_name, store_name, 'tag_images', new_filename)
    except Exception:
        return os.path.join('tag_images', 'orphaned', filename)

# =================================================================
# 2. CORE MODELS
# =================================================================

class Company(AuditModel):
    """Represents a client organization that owns one or more stores."""
    name = models.CharField(max_length=255)
    owner_name = models.CharField(max_length=255, blank=True, null=True)
    mailing_address = models.TextField(blank=True, null=True)
    contact_email = models.EmailField(blank=True, null=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    tax_id = models.CharField(max_length=50, blank=True, null=True, verbose_name="Tax/VAT ID")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name
    
    class Meta:
        verbose_name_plural = "Companies"

    def save(self, *args, **kwargs):
        """Cascade deactivation to stores and gateways if company is deactivated."""
        if self.pk:
            old_instance = Company.objects.get(pk=self.pk)
            if old_instance.is_active and not self.is_active:
                self.stores.all().update(is_active=False)
                Gateway.objects.filter(store__company=self).update(is_active=False)
        super().save(*args, **kwargs)

class User(AbstractUser, AuditModel):
    """Custom user model supporting multi-tenant access via company and store assignments."""
    company = models.ForeignKey(
        'Company', 
        on_delete=models.CASCADE, 
        related_name='users', 
        null=True, 
        blank=True
    )
    managed_stores = models.ManyToManyField(
        'Store', 
        blank=True, 
        related_name='managers',
        help_text="The specific stores this user can manage."
    )
    ROLE_CHOICES = [
        ('admin', 'Global Admin'),
        ('owner', 'Company Owner'),
        ('manager', 'Store Manager'),
        ('staff', 'Store Staff'),
        ('readonly', 'Read-Only User'),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='admin')

# =================================================================
# 3. TENANT & HARDWARE MODELS
# =================================================================

class Store(AuditModel):
    """Represents a physical retail location."""
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='stores')
    name = models.CharField(max_length=255)
    location_code = models.CharField(max_length=50) 
    is_active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        """Cascade deactivation to gateways if store is deactivated."""
        if self.pk:
            old_instance = Store.objects.get(pk=self.pk)
            if old_instance.is_active and not self.is_active:
                self.gateways.all().update(is_active=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.company.name} - {self.name}"

class TagHardware(AuditModel):
    """Technical specifications for different ESL hardware models."""
    model_number = models.CharField(max_length=100, unique=True)
    width_px = models.IntegerField()
    height_px = models.IntegerField()
    color_scheme = models.CharField(
        max_length=10, 
        choices=[('BW', 'B&W'), ('BWR', 'BWR'), ('BWRY', 'BWRY')],
        default='BWRY'
    )
    display_size_inch = models.DecimalField(max_digits=4, decimal_places=2)

    def __str__(self):
        return f"{self.model_number}"

class Gateway(AuditModel):
    """Communication hub that manages a set of ESL tags in a store."""
    objects = StoreManager()
    estation_id = models.CharField(max_length=4, unique=True, null=True, blank=True, verbose_name="Gateway ID")
    name = models.CharField(max_length=255, blank=True, null=True, help_text="Logical name for the gateway")
    alias = models.CharField(max_length=2, blank=True, null=True)
    is_online = models.BooleanField(default=False)
    gateway_mac = models.CharField(max_length=100, unique=True, verbose_name="MAC Address")

    # Connection details
    gateway_ip = models.GenericIPAddressField(null=True, blank=True, verbose_name="Gateway IP")
    app_server_ip = models.GenericIPAddressField(null=True, blank=True, verbose_name="Application Server IP")
    app_server_port = models.IntegerField(null=True, blank=True, verbose_name="Application Server Port")

    username = models.CharField(max_length=100, blank=True, null=True)
    password = models.CharField(max_length=100, blank=True, null=True)

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='gateways')
    is_active = models.BooleanField(default=True)

    last_heartbeat = models.DateTimeField(null=True, blank=True)
    last_successful_heartbeat = models.DateTimeField(null=True, blank=True)
    last_seen = models.DateTimeField(auto_now=True)

    def __str__(self):
        name_str = f" - {self.name}" if self.name else ""
        return f"{self.estation_id or 'No ID'}{name_str} ({self.store.name if self.store else 'No Store'})"

class Supplier(models.Model):
    """Supplier info for products, used on ESL tag display."""
    name = models.CharField(max_length=100, unique=True)
    abbreviation = models.CharField(max_length=3, unique=True, help_text="3 character code (e.g., GSC, STM)")
    
    def __str__(self):
        return f"{self.name} ({self.abbreviation})"

class Product(AuditModel):
    """Product catalog item for a specific store."""
    objects = StoreManager()
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name='products')
    sku = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_on_special = models.BooleanField(default=False)
    preferred_supplier = models.ForeignKey(
        Supplier, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='products'
    )
    
    class Meta:
        unique_together = ('sku', 'store')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_data = {
            'price': self.price,
            'name': self.name,
            'is_on_special': self.is_on_special,
            'preferred_supplier_id': self.preferred_supplier_id,
        }

    def __str__(self):
        return f"{self.sku} - {self.name}"

    def save(self, *args, **kwargs):
        """Detection logic to trigger tag image updates only on relevant changes."""
        trigger_refresh = False
        if self.pk:
            if (self._original_data['price'] != self.price or
                self._original_data['name'] != self.name or
                self._original_data['is_on_special'] != self.is_on_special or
                self._original_data['preferred_supplier_id'] != self.preferred_supplier_id):
                trigger_refresh = True
        else:
            trigger_refresh = True

        super().save(*args, **kwargs)
        
        if trigger_refresh:
            from .tasks import update_tag_image_task
            for tag in self.esl_tags.all():
                update_tag_image_task.delay(tag.id)


class ESLTag(AuditModel):
    """Electronic Shelf Label device and its association with a product."""
    objects = StoreManager()
    SYNC_STATES = [
        ('IDLE', 'No Pending Tasks'),
        ('PROCESSING', 'Generating Image...'),
        ('IMAGE_READY', 'Image Prepared'),
        ('PUSHED', 'Sent to Gateway'),
        ('SUCCESS', 'Update Confirmed'),
        ('GEN_FAILED', 'Image Generation Failed'),
        ('PUSH_FAILED', 'Gateway Delivery Failed'),
        ('FAILED', 'General Failure'),
    ]
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='esl_tags', null=True)
    gateway = models.ForeignKey(Gateway, on_delete=models.SET_NULL, related_name='tags', null=True, blank=True)
    last_successful_gateway_id = models.CharField(max_length=4, blank=True, null=True, verbose_name="Last Successful Gateway ID")
    tag_mac = models.CharField(max_length=50, verbose_name="Tag ID/MAC")
    hardware_spec = models.ForeignKey(TagHardware, on_delete=models.SET_NULL, null=True)
    paired_product = models.ForeignKey(
        Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='esl_tags'
    )
    tag_image = models.ImageField(
        upload_to=get_tag_path, 
        storage=OverwriteStorage(),
        null=True, 
        blank=True
    )
    last_image_gen_success = models.DateTimeField(null=True, blank=True)
    sync_state = models.CharField(max_length=20, choices=SYNC_STATES, default='IDLE')
    last_image_task_id = models.CharField(max_length=255, null=True, blank=True)
    last_image_task_token = models.IntegerField(null=True, blank=True)

    battery_level = models.IntegerField(default=100)
    aisle = models.CharField(max_length=20, blank=True, null=True)
    section = models.CharField(max_length=20, blank=True, null=True)
    shelf_row = models.CharField(max_length=20, blank=True, null=True)

    TEMPLATE_CHOICES = [(1, 'Standard (V1)'), (2, 'Promo (V2)'), (3, 'Modern (V3)')]
    template_id = models.IntegerField(choices=TEMPLATE_CHOICES, default=1)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_data = {
            'paired_product_id': self.paired_product_id,
            'template_id': self.template_id,
            'hardware_spec_id': self.hardware_spec_id,
        }

    def clean(self):
        if self.gateway and self.store and self.gateway.store != self.store:
            raise ValidationError("Gateway Mismatch: Gateway must belong to the same store as the Tag.")
        if self.paired_product and self.store and self.store != self.paired_product.store:
            raise ValidationError("Store Mismatch: Product and Tag must be in the same store.")

    def save(self, *args, **kwargs):
        """Triggers image generation on pairing or template changes."""
        if self.gateway and not self.store:
            self.store = self.gateway.store

        trigger_refresh = False
        if self.pk:
            if (self._original_data['paired_product_id'] != self.paired_product_id or
                self._original_data['template_id'] != self.template_id or
                self._original_data['hardware_spec_id'] != self.hardware_spec_id):
                trigger_refresh = True
        else:
            if self.paired_product_id:
                trigger_refresh = True

        self.full_clean()
        super().save(*args, **kwargs)

        if trigger_refresh:
            from .tasks import update_tag_image_task
            update_tag_image_task.delay(self.id)

    def __str__(self):
        return f"{self.tag_mac} ({self.store.name if self.store else 'No Store'}) -> {self.paired_product.name if self.paired_product else 'Unpaired'}"

    class Meta:
        unique_together = ('tag_mac', 'store')
        verbose_name = "ESL Tag"
        verbose_name_plural = "ESL Tags"
