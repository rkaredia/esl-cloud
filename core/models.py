from django.db import models
from django.utils import timezone
from django.contrib.auth.models import AbstractUser
import os
from django.utils.text import slugify
from django.conf import settings
from .storage import OverwriteStorage
from django.core.exceptions import ValidationError
from .managers import StoreManager

"""
SAIS CORE MODELS: DATA ARCHITECTURE & RELATIONSHIPS
---------------------------------------------------
In Django, 'Models' are the single, definitive source of truth about your data.
If you are familiar with Data Warehousing:
- A Model class maps to a Database Table.
- A Class Attribute (e.g., name = models.CharField) maps to a Table Column.
- An Instance of a Model (e.g., my_company = Company()) maps to a Table Row.

This project follows a Multi-Tenant architecture, meaning data for multiple
companies and stores is stored in the same tables but isolated logically
via 'company' and 'store' foreign key references.
"""

# =================================================================
# 1. BASE CLASSES & UTILS (Reusable data patterns)
# =================================================================

class AuditModel(models.Model):
    """
    EDUCATIONAL: This is an 'Abstract Base Class'. It doesn't create a table
    itself in the DB, but other models inherit its fields.
    In Data Warehousing, these are standard 'Audit Dimensions' used to
    track when data was created or changed and by whom.
    """
    # auto_now_add: Sets the timestamp once when the row is first created.
    created_at = models.DateTimeField(auto_now_add=True)

    # auto_now: Updates the timestamp every time the row is saved (updated).
    updated_at = models.DateTimeField(auto_now=True)

    # ForeignKey: Creates a many-to-one relationship.
    # Many rows in this table can point to one User in the Auth table.
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, # If the user is deleted, keep the record but set this to NULL.
        null=True, 
        blank=True,
        related_name="%(class)s_updates" # Dynamic naming for reverse lookups (e.g., user.company_updates.all())
    )

    class Meta:
        # abstract = True tells Django NOT to create a table for this class.
        abstract = True

def get_tag_path(instance, filename):
    """
    DYNAMIC FILE PATH GENERATOR
    ---------------------------
    Determines where an ESL image should be stored on the disk.
    Organizes files as: /media/<company-slug>/<store-slug>/tag_images/<mac>.bmp
    This keeps the storage bucket clean and facilitates bulk backups/purges.
    """
    try:
        # Step 1: Find the store associated with this tag
        store = instance.store
        if not store and instance.gateway:
            store = instance.gateway.store

        # Step 2: Slugify names (removes spaces/special chars) for safe folder names
        company_name = slugify(store.company.name)
        store_name = slugify(store.name)

        # Step 3: Extract file extension (e.g., .bmp)
        ext = os.path.splitext(filename)[1]

        # Step 4: Rename file to the Tag's MAC address (unique identifier)
        new_filename = f"{instance.tag_mac.replace(':', '')}{ext}"

        # Resulting path: 'my-company/store-1/tag_images/AABBCCDDEEFF.bmp'
        return os.path.join(company_name, store_name, 'tag_images', new_filename)
    except Exception:
        # Fallback for orphaned records
        return os.path.join('tag_images', 'orphaned', filename)

# =================================================================
# 2. CORE MODELS (Entity Definitions)
# =================================================================

class Company(AuditModel):
    """
    THE TOP-LEVEL TENANT
    -------------------
    Represents the business entity that owns stores.
    In the data hierarchy, this is the root of most relationships.
    """
    name = models.CharField(max_length=255)
    owner_name = models.CharField(max_length=255, blank=True, null=True)
    mailing_address = models.TextField(blank=True, null=True)
    contact_email = models.EmailField(blank=True, null=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    tax_id = models.CharField(max_length=50, blank=True, null=True, verbose_name="Tax/VAT ID")

    # Soft Delete / Active State: Instead of deleting rows (which breaks history),
    # we toggle this boolean to hide records from the UI.
    is_active = models.BooleanField(default=True)

    def __str__(self):
        # This determines how the object is named in the Admin UI and logs.
        return self.name
    
    class Meta:
        verbose_name_plural = "Companies" # Corrects the pluralization in the Admin sidebar

    def save(self, *args, **kwargs):
        """
        CUSTOM SAVE LOGIC (Interceptor Pattern)
        ---------------------------------------
        When a company is deactivated, we want to automatically deactivate
        all of its stores as well (Cascading Deactivation).
        """
        if self.pk: # If the record already exists (has a Primary Key)
            old_instance = Company.objects.get(pk=self.pk)
            # If it was active and is now being set to inactive:
            if old_instance.is_active and not self.is_active:
                # Update all related stores in one efficient SQL query
                self.stores.all().update(is_active=False)

        # Call the original save() to actually write to the database
        super().save(*args, **kwargs)

class User(AbstractUser, AuditModel):
    """
    CUSTOM USER & AUTHENTICATION
    ----------------------------
    Extends Django's built-in User with multi-tenant company/store links.
    """
    company = models.ForeignKey(
        'Company', 
        on_delete=models.CASCADE, # If company is deleted, delete all users in it.
        related_name='users', 
        null=True, 
        blank=True
    )

    # ManyToManyField: Creates a bridge table (Join Table) in the DB.
    # Allows one user to manage multiple stores, and one store to have multiple managers.
    managed_stores = models.ManyToManyField(
        'Store', 
        blank=True, 
        related_name='managers',
        help_text="The specific stores this user can manage."
    )

    ROLE_CHOICES = [
        ('admin', 'Global Admin'),   # Full system access
        ('owner', 'Company Owner'),  # Access to all company stores
        ('manager', 'Store Manager'),# Access to assigned stores
        ('staff', 'Store Staff'),    # Operational access
        ('readonly', 'Read-Only User'),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='admin')

# =================================================================
# 3. TENANT & HARDWARE MODELS
# =================================================================

class Store(AuditModel):
    """
    THE OPERATIONAL HUB
    -------------------
    A physical retail location. Most operational data (Products, Tags, Gateways)
    is siloed by Store.
    """
    # Foreign Key to Company (Many-to-One)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='stores')
    name = models.CharField(max_length=255)
    location_code = models.CharField(max_length=50) 
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.company.name} - {self.name}"

class GlobalSetting(models.Model):
    """
    SYSTEM CONFIGURATION KEY-VALUE STORE
    -------------------------------------
    A simple table for runtime parameters (e.g., 'LOG_RETENTION_DAYS').
    Similar to an Environment Variables table in a Data Warehouse.
    """
    key = models.CharField(max_length=100, unique=True)
    value = models.TextField()
    description = models.TextField(blank=True)

    def __str__(self):
        return self.key

class TagHardware(AuditModel):
    """
    HARDWARE SPECIFICATION REGISTRY
    -------------------------------
    Acts as a 'Dimension Table' for ESL hardware types.
    Defines the physical resolution and capabilities of the labels.
    """
    model_number = models.CharField(max_length=100, unique=True)
    width_px = models.IntegerField() # e.g. 250px
    height_px = models.IntegerField() # e.g. 122px
    color_scheme = models.CharField(
        max_length=10, 
        choices=[('BW', 'B&W'), ('BWR', 'BWR'), ('BWRY', 'BWRY')],
        default='BWRY'
    )
    display_size_inch = models.DecimalField(max_digits=4, decimal_places=2) # e.g. 2.13

    def __str__(self):
        return f"{self.model_number}"

class Gateway(AuditModel):
    """
    IOT COMMUNICATION HUB (eStation)
    --------------------------------
    Represents the physical base station that communicates with the ESL tags.
    Every heartbeat or command sent to tags goes through a Gateway.
    """
    # Custom Manager for automatic store-level filtering
    objects = StoreManager()

    is_active = models.BooleanField(default=True)
    estation_id = models.CharField(max_length=4, unique=True, null=True, blank=True, verbose_name="Gateway ID")
    name = models.CharField(max_length=255, blank=True, null=True, help_text="Logical name for the gateway")
    alias = models.CharField(max_length=2, blank=True, null=True)

    STATUS_CHOICES = [
        ('ONLINE', 'Online'),
        ('OFFLINE', 'Offline'),
        ('ERROR', 'Error'),
    ]
    is_online = models.CharField(max_length=10, choices=STATUS_CHOICES, default='OFFLINE')
    gateway_mac = models.CharField(max_length=100, unique=True, verbose_name="MAC Address")

    # Network Connection Details (Source of truth for MQTT handshakes)
    gateway_ip = models.GenericIPAddressField(null=True, blank=True, verbose_name="Gateway IP")
    app_server_ip = models.GenericIPAddressField(null=True, blank=True, verbose_name="Application Server IP")
    app_server_port = models.IntegerField(null=True, blank=True, verbose_name="Application Server Port")

    # Gateway Login Credentials
    username = models.CharField(max_length=100, blank=True, null=True)
    password = models.CharField(max_length=100, blank=True, null=True)

    # Hardware Metadata (Updated via MQTT Heartbeats)
    ap_type = models.IntegerField(null=True, blank=True, verbose_name="AP Type")
    ap_version = models.CharField(max_length=50, blank=True, null=True, verbose_name="Base Station Version")
    module_version = models.CharField(max_length=255, blank=True, null=True, verbose_name="Bluetooth Module Version")
    disk_size = models.IntegerField(null=True, blank=True, verbose_name="Disk Size (MB)")
    free_space = models.IntegerField(null=True, blank=True, verbose_name="Free Space (MB)")
    heartbeat_interval = models.IntegerField(null=True, blank=True, verbose_name="Heartbeat Interval (sec)")
    is_encrypt_enabled = models.BooleanField(default=True, verbose_name="Encryption Enabled")

    # Status & Error tracking
    tags_queued_count = models.IntegerField(default=0, verbose_name="Tags Queued")
    tags_comm_count = models.IntegerField(default=0, verbose_name="Tags in Communication")
    last_error_message = models.TextField(blank=True, null=True, verbose_name="Last Error Message")
    last_error_code = models.IntegerField(null=True, blank=True, verbose_name="Last Error Code")
    last_error_timestamp = models.DateTimeField(null=True, blank=True, verbose_name="Last Error Timestamp")

    # Static IP Configuration
    is_auto_ip = models.BooleanField(default=True, verbose_name="Auto IP (DHCP)")
    local_ip = models.GenericIPAddressField(null=True, blank=True, verbose_name="Static Local IP")
    netmask = models.GenericIPAddressField(null=True, blank=True, verbose_name="Subnet Mask")
    network_gateway = models.GenericIPAddressField(null=True, blank=True, verbose_name="Network Gateway")

    # Multi-Tenant relationship
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='gateways')

    # Monitoring Timestamps
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    last_successful_heartbeat = models.DateTimeField(null=True, blank=True)
    last_seen = models.DateTimeField(auto_now=True)

    MESSAGE_CODES = {
        1: "OK",
        2: "Idle",
        3: "Result",
        4: "Heartbeat",
        5: "ModError",
        6: "AppError",
        7: "Busy",
        8: "MaxLimit",
        9: "InvalidTaskESL",
        10: "InvalidTaskDSL",
        11: "InvalidConfig",
        12: "InvalidOTA",
    }

    def get_real_time_status(self):
        """
        REAL-TIME STATUS CALCULATION
        ----------------------------
        Returns a tuple of (status_code, status_label, color)
        """
        interval = self.heartbeat_interval or 15
        timeout_seconds = interval * 4

        if not self.last_heartbeat or self.last_heartbeat < (timezone.now() - timezone.timedelta(seconds=timeout_seconds)):
            return ('OFFLINE', 'No Heartbeat', '#dc2626') # Red

        # If we have a recent heartbeat, use the last known state/code
        label = self.MESSAGE_CODES.get(self.last_error_code, "Online")

        if self.is_online == 'ERROR':
            return ('ERROR', f"Error: {label}", '#f59e0b') # Amber/Orange

        return ('ONLINE', f"Online ({label})", '#059669') # Green

    def is_currently_online(self):
        """Helper for simple boolean checks, keeps compatibility with older logic."""
        status, _, _ = self.get_real_time_status()
        return status != 'OFFLINE'

    def __str__(self):
        name_str = f" - {self.name}" if self.name else ""
        return f"{self.estation_id or 'No ID'}{name_str} ({self.store.name if self.store else 'No Store'})"

class Supplier(models.Model):
    """
    PRODUCT SUPPLIER REGISTRY
    -------------------------
    Dimension table for brands/suppliers. The abbreviation is often
    displayed on the ESL tag image.
    """
    name = models.CharField(max_length=100, unique=True)
    abbreviation = models.CharField(max_length=3, unique=True, help_text="3 character code (e.g., GSC, STM)")
    
    def __str__(self):
        return f"{self.name} ({self.abbreviation})"

class Product(AuditModel):
    """
    RETAIL PRODUCT RECORD
    ---------------------
    The core business entity. Contains pricing and marketing info
    that will eventually be rendered onto a physical shelf label.
    """
    objects = StoreManager()

    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name='products')
    sku = models.CharField(max_length=50) # Stock Keeping Unit (Unique per store)
    name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2) # Precise financial data
    is_on_special = models.BooleanField(default=False)

    preferred_supplier = models.ForeignKey(
        Supplier, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='products'
    )
    
    class Meta:
        # DB-Level Constraint: Prevent two products from having the same SKU in the same Store.
        unique_together = ('sku', 'store')

    def __init__(self, *args, **kwargs):
        """
        CONSTRUCTOR OVERRIDE
        --------------------
        We snapshot the original data when the object is loaded from the DB.
        This allows us to compare and see IF a field like 'price' actually
        changed when save() is called.
        """
        super().__init__(*args, **kwargs)
        self._original_data = {
            'price': self.__dict__.get('price'),
            'name': self.__dict__.get('name'),
            'is_on_special': self.__dict__.get('is_on_special'),
            'preferred_supplier_id': self.__dict__.get('preferred_supplier_id'),
        }

    def __str__(self):
        return f"{self.sku} - {self.name}"

    def save(self, *args, **kwargs):
        """
        CHANGE DETECTION LOGIC
        ----------------------
        We only want to trigger an expensive 'Tag Image Refresh' task
        if fields that appear on the label (Price, Name, Promo status)
        have actually been modified.
        """
        trigger_refresh = False
        if self.pk: # If updating existing record
            if (self._original_data['price'] != self.price or
                self._original_data['name'] != self.name or
                self._original_data['is_on_special'] != self.is_on_special or
                self._original_data['preferred_supplier_id'] != self.preferred_supplier_id):
                trigger_refresh = True
        else: # If creating new record
            trigger_refresh = True

        super().save(*args, **kwargs)
        
        # NOTE: Task triggering is handled by 'signals.py' post_save receivers
        # to keep this model code lean and avoid circular imports.


class ESLTag(AuditModel):
    """
    ELECTRONIC SHELF LABEL (ESL) DEVICE
    -----------------------------------
    Represents the physical digital label hardware.
    This model acts as the 'Mapping Table' between a Product and the
    Hardware device.
    """
    objects = StoreManager()

    # Life-cycle states of an ESL Update
    SYNC_STATES = [
        ('IDLE', 'No Pending Tasks'),
        ('PROCESSING', 'Generating Image...'),
        ('IMAGE_READY', 'Image Prepared'),
        ('PUSHED', 'Sent to Gateway'),
        ('RETRY_WAITING', 'Waiting for Retry'),
        ('SUCCESS', 'Update Confirmed'),
        ('GEN_FAILED', 'Image Generation Failed'),
        ('PUSH_FAILED', 'Gateway Delivery Failed'),
        ('FAILED', 'General Failure'),
    ]

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='esl_tags', null=True)

    # Logical Binding: Which gateway should this tag talk to?
    gateway = models.ForeignKey(Gateway, on_delete=models.SET_NULL, related_name='tags', null=True, blank=True)
    last_successful_gateway_id = models.CharField(max_length=4, blank=True, null=True, verbose_name="Last Successful Gateway ID")

    # The physical Hardware ID (MAC Address)
    tag_mac = models.CharField(max_length=50, verbose_name="Tag ID/MAC")

    # Link to hardware specs (Width/Height)
    hardware_spec = models.ForeignKey(TagHardware, on_delete=models.SET_NULL, null=True)

    # PRODUCT PAIRING: This is the 'Logic Link' that determines what content is on the tag.
    paired_product = models.ForeignKey(
        Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='esl_tags'
    )

    # Image Management
    tag_image = models.ImageField(
        upload_to=get_tag_path, 
        storage=OverwriteStorage(),
        null=True, 
        blank=True
    )
    last_image_gen_success = models.DateTimeField(null=True, blank=True)
    sync_state = models.CharField(max_length=20, choices=SYNC_STATES, default='IDLE')

    # Background Task Tracking (for Celery)
    last_image_task_id = models.CharField(max_length=255, null=True, blank=True)
    last_image_task_token = models.IntegerField(null=True, blank=True)
    last_pushed_at = models.DateTimeField(null=True, blank=True, verbose_name="Last Pushed to Gateway")
    retry_count = models.IntegerField(default=0)

    # Hardware Status (Telemetery)
    battery_level = models.IntegerField(default=100)
    aisle = models.CharField(max_length=20, blank=True, null=True)
    section = models.CharField(max_length=20, blank=True, null=True)
    shelf_row = models.CharField(max_length=20, blank=True, null=True)

    # Visual Layout
    TEMPLATE_CHOICES = [(1, 'Standard (V1)'), (2, 'Promo (V2)'), (3, 'Modern (V3)')]
    template_id = models.IntegerField(choices=TEMPLATE_CHOICES, default=1)

    def __init__(self, *args, **kwargs):
        # Snapshot state for change detection (same pattern as Product model)
        super().__init__(*args, **kwargs)
        self._original_data = {
            'paired_product_id': self.__dict__.get('paired_product_id'),
            'template_id': self.__dict__.get('template_id'),
            'hardware_spec_id': self.__dict__.get('hardware_spec_id'),
        }

    def clean(self):
        """
        MODEL VALIDATION
        ----------------
        Enforces business rules at the application level before saving to DB.
        Ensures a Tag and its Gateway/Product all belong to the same Store.
        """
        if self.gateway and self.store and self.gateway.store != self.store:
            raise ValidationError("Gateway Mismatch: Gateway must belong to the same store as the Tag.")
        if self.paired_product and self.store and self.store != self.paired_product.store:
            raise ValidationError("Store Mismatch: Product and Tag must be in the same store.")

    def save(self, *args, **kwargs):
        """
        AUTO-LOGIC ON SAVE
        ------------------
        Ensures data integrity (Store inheritance) and detects pairing changes.
        """
        # Normalize MAC Address to UPPERCASE (Fixes Case Sensitivity issues)
        if self.tag_mac:
            self.tag_mac = self.tag_mac.strip().upper()

        # Rule: If a tag is assigned to a gateway, it must inherit that gateway's store.
        if self.gateway and not self.store:
            self.store = self.gateway.store

        # Detect changes that require a physical refresh of the ESL screen.
        trigger_refresh = False
        if self.pk:
            if (self._original_data['paired_product_id'] != self.paired_product_id or
                self._original_data['template_id'] != self.template_id or
                self._original_data['hardware_spec_id'] != self.hardware_spec_id):
                trigger_refresh = True

            # Reset image and status if product is removed
            if self._original_data['paired_product_id'] and not self.paired_product_id:
                self.tag_image = None
                self.sync_state = 'IDLE'
                self.last_image_gen_success = None
        else:
            if self.paired_product_id:
                trigger_refresh = True

        # Run full_clean() manually as Django models don't call it automatically on save()
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tag_mac} ({self.store.name if self.store else 'No Store'}) -> {self.paired_product.name if self.paired_product else 'Unpaired'}"

    class Meta:
        # Constraint: MAC addresses are unique only within a Store (Physical tagging rule)
        unique_together = ('tag_mac', 'store')
        verbose_name = "ESL Tag"
        verbose_name_plural = "ESL Tags"


class MQTTMessage(models.Model):
    """
    COMMUNICATION LOG (EVENT LOG)
    -----------------------------
    A 'Fact Table' capturing every single packet sent or received from the Gateways.
    Crucial for auditing and troubleshooting hardware connectivity.
    """
    DIRECTION_CHOICES = [('sent', 'Sent'), ('received', 'Received')]

    timestamp = models.DateTimeField(auto_now_add=True)
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    estation_id = models.CharField(max_length=50, verbose_name="Gateway ID")
    topic = models.CharField(max_length=255)
    data = models.TextField(help_text="JSON payload") # Stores the raw message body
    is_success = models.BooleanField(default=True)

    class Meta:
        verbose_name = "MQTT Message"
        verbose_name_plural = "MQTT Messages"
        ordering = ['-timestamp'] # Newest first

    def __str__(self):
        return f"{self.direction.upper()} | {self.estation_id} | {self.topic} | {self.timestamp}"
