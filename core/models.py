from django.db import models
from django.contrib.auth.models import AbstractUser
import os
from django.utils.text import slugify
from django.conf import settings
from .storage import OverwriteStorage
from django.core.exceptions import ValidationError

# =================================================================
# 1. BASE AUDIT CLASS & STORAGE INITIALIZATION
# =================================================================

# Initialize the custom storage for overwriting files
overwrite_storage = OverwriteStorage()

class BaseAuditModel(models.Model):
    """
    Abstract base class. Models inheriting this will automatically
    have 'last_updated' and 'updated_by'.
    """
    last_updated = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True
    )

    class Meta:
        abstract = True


# =================================================================
# 2. CORE MODELS
# =================================================================

class Company(models.Model):
    name = models.CharField(max_length=255)
    # New Information Fields
    owner_name = models.CharField(max_length=255, blank=True, null=True)
    mailing_address = models.TextField(blank=True, null=True)
    contact_email = models.EmailField(blank=True, null=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    tax_id = models.CharField(max_length=50, blank=True, null=True, verbose_name="Tax/VAT ID")
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
    
    class Meta:
        verbose_name_plural = "Companies"

class User(AbstractUser):
    company = models.ForeignKey(
        'Company', 
        on_delete=models.CASCADE, 
        related_name='users', 
        null=True, 
        blank=True
    )
    # Changed to ManyToMany to allow managing multiple stores
    managed_stores = models.ManyToManyField(
        'Store', 
        blank=True, 
        related_name='managers',
        help_text="The specific stores this user can manage."
    )

    ROLE_CHOICES = [
        ('admin', 'Global Admin'),
        ('owner', 'Company Owner'), # Add this
        ('manager', 'Store Manager'),
        ('readonly', 'Read-Only User'),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='admin')

    def __str__(self):
        if self.company:
            return f"{self.company.name} : {self.username}"
        return f"Global Admin : {self.username}"

# =================================================================
# 3. TENANT & HARDWARE MODELS
# =================================================================

class Store(BaseAuditModel):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='stores')
    name = models.CharField(max_length=255)
    location_code = models.CharField(max_length=50) 
    
    def __str__(self):
        return f"{self.company.name} - {self.name}"

class TagHardware(models.Model):
    """
    Hardware specifications for different tag models.
    """
    # Changed back to model_number to maintain compatibility with admin.py
    model_number = models.CharField(max_length=100, unique=True, help_text="e.g., ET0290-85")
    width_px = models.IntegerField(help_text="Width in pixels")
    height_px = models.IntegerField(help_text="Height in pixels")
    color_scheme = models.CharField(
        max_length=10, 
        choices=[('BW', 'B&W'), ('BWR', 'BWR'), ('BWRY', 'BWRY')],
        default='BWRY'
    )
    display_size_inch = models.DecimalField(max_digits=4, decimal_places=2, help_text="e.g., 2.90")

    def __str__(self):
        return f"{self.model_number} ({self.width_px}x{self.height_px})"

class Gateway(BaseAuditModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE)
    gateway_mac = models.CharField(max_length=17, unique=True)
    is_online = models.BooleanField(default=False)

    def __str__(self):
        return f"Gateway {self.gateway_mac} ({self.store.name})"

class Product(BaseAuditModel):
    store = models.ForeignKey('Store', on_delete=models.PROTECT, related_name='products')
    sku = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    # NEW: Special pricing toggle for different templates
    is_on_special = models.BooleanField(default=False, verbose_name="On Special / Promotion")

    def __str__(self):
        return f"{self.sku} : {self.name}"

    class Meta:
        unique_together = ('sku', 'store')

# =================================================================
# 4. ESL TAG & PATH LOGIC
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

class ESLTag(BaseAuditModel):

    gateway = models.ForeignKey(Gateway, on_delete=models.CASCADE)
    tag_mac = models.CharField(max_length=50, unique=True)
    
    # ForeignKey to TagHardware
    hardware_spec = models.ForeignKey(TagHardware, on_delete=models.SET_NULL, null=True)
    
    # NEW: Physical Location Information
    aisle = models.CharField(max_length=50, blank=True, null=True, help_text="e.g., Aisle 4")
    section = models.CharField(max_length=50, blank=True, null=True, help_text="e.g., Dairy")
    shelf_row = models.CharField(max_length=50, blank=True, null=True, help_text="e.g., Row 2")
    
    paired_product = models.ForeignKey(
        'Product', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='esl_tags'
    )
    
    battery_level = models.IntegerField(default=100)
    last_seen = models.DateTimeField(auto_now=True)

    tag_image = models.ImageField(
        upload_to=get_tag_path, 
        storage=overwrite_storage,
        null=True, 
        blank=True
    )

    def clean(self):
        if self.paired_product and self.gateway.store != self.paired_product.store:
            raise ValidationError(f"Store Mismatch.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tag_mac} ({self.aisle or 'No Loc'})"