from django.db import models
from django.contrib.auth.models import AbstractUser
import os
from django.utils.text import slugify
from django.conf import settings
from .storage import OverwriteStorage
from django.core.exceptions import ValidationError

# =================================================================
# 1. BASE AUDIT CLASS
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
        related_name="%(class)s_updates"  # Add this specific line
    )

    class Meta:
        abstract = True

# =================================================================
# functions
# =================================================================
def get_tag_path(instance, filename):
    try:
        company_name = slugify(instance.gateway.store.company.name)
        store_name = slugify(instance.gateway.store.name)
        # Ensure filenames are based on MAC for easy identification
        ext = os.path.splitext(filename)[1]
        new_filename = f"{instance.tag_mac.replace(':', '')}{ext}"
        return os.path.join(company_name, store_name, 'tag_images', new_filename)
    except Exception:
        return os.path.join('tag_images', 'orphaned', filename)

# =================================================================
# 2. CORE MODELS
# =================================================================

class Company(AuditModel):
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
        # We check if is_active changed to False
        if self.pk:
            old_instance = Company.objects.get(pk=self.pk)
            if old_instance.is_active and not self.is_active:
                # Cascade deactivation
                self.stores.all().update(is_active=False)
                Gateway.objects.filter(store__company=self).update(is_active=False)
        super().save(*args, **kwargs)

class User(AbstractUser, AuditModel):
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
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='stores')
    name = models.CharField(max_length=255)
    location_code = models.CharField(max_length=50) 
    is_active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        if self.pk:
            old_instance = Store.objects.get(pk=self.pk)
            if old_instance.is_active and not self.is_active:
                self.gateways.all().update(is_active=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.company.name} - {self.name}"

class TagHardware(AuditModel):
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
    is_online = models.BooleanField(default=False)
    gateway_mac = models.CharField(max_length=100, unique=True)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='gateways')
    is_active = models.BooleanField(default=True)
    last_seen = models.DateTimeField(auto_now=True)
    def __str__(self):
        return f"{self.gateway_mac} ({self.store.name if self.store else 'No Store'})"

class Product(AuditModel):
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name='products')
    sku = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_on_special = models.BooleanField(default=False)

    class Meta:
        unique_together = ('sku', 'store')
    def __str__(self):
        # This will change "Product object (4)" to "SKU - Name"
        return f"{self.sku} - {self.name}"



class ESLTag(AuditModel):
    gateway = models.ForeignKey(Gateway, on_delete=models.CASCADE, related_name='tags')
    tag_mac = models.CharField(max_length=50, unique=True)
    hardware_spec = models.ForeignKey(TagHardware, on_delete=models.SET_NULL, null=True)
    paired_product = models.ForeignKey(
        Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='esl_tags'
    )
    # Location fields...
    battery_level = models.IntegerField(default=100)
#    tag_image = models.ImageField(upload_to='tag_images/', null=True, blank=True)
    tag_image = models.ImageField(
        upload_to=get_tag_path, 
        storage=OverwriteStorage(),
        null=True, 
        blank=True
    )
    
    # NEW: Physical Location Information
    aisle = models.CharField(max_length=20, blank=True, null=True, help_text="e.g., Aisle 4")
    section = models.CharField(max_length=20, blank=True, null=True, help_text="e.g., Dairy")
    shelf_row = models.CharField(max_length=20, blank=True, null=True, help_text="e.g., Row 2")


    def clean(self):
        if self.paired_product and self.gateway.store != self.paired_product.store:
            raise ValidationError("Store Mismatch: Product and Tag must be in the same store.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


#---------------

