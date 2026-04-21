from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from core.models import User, Company, Store, TagHardware, Gateway, ESLTag, Product, GlobalSetting
from decimal import Decimal

"""
MANAGEMENT COMMAND: DATABASE SEEDER
-----------------------------------
This script populates the database with initial required data.
It's essential for setting up a fresh development environment or a new production node.

It performs 7 major steps:
1. Registers supported ESL hardware specifications (resolution, color).
2. Creates Django permission groups (Owner, Manager, Staff, Read-Only).
3. Sets up a default Company and Store.
4. Creates the root 'admin' superuser.
5. Creates a 'test_owner' account for operational testing.
6. Registers a dummy gateway to ensure the system starts up.
7. Populates Global Settings (encryption keys, retention periods).

USAGE: python manage.py seed_data
"""

class Command(BaseCommand):
    help = "Seeds hardware specs, roles, base company data, and monitoring permissions."

    def handle(self, *args, **options):
        # 1. SEED HARDWARE CATALOG
        # Maps the physical models (D21 series) to their screen resolutions.
        specs = [
            {'model_number': 'ET0213-81', 'width': 250, 'height': 122, 'size': 2.13, 'colors': 'BWRY'},
            {'model_number': 'ET0213-36', 'width': 250, 'height': 122, 'size': 2.13, 'colors': 'BWR'},
            {'model_number': 'ET0213-39', 'width': 250, 'height': 122, 'size': 2.13, 'colors': 'BW'},
            {'model_number': 'ET0266-82', 'width': 296, 'height': 152, 'size': 2.66, 'colors': 'BWRY'},
            {'model_number': 'ET0266-36', 'width': 296, 'height': 152, 'size': 2.66, 'colors': 'BWR'},
            {'model_number': 'ET0266-39', 'width': 296, 'height': 152, 'size': 2.66, 'colors': 'BW'},
            {'model_number': 'ET0290-85', 'width': 384, 'height': 168, 'size': 2.90, 'colors': 'BWRY'},
            {'model_number': 'ET0290-84', 'width': 296, 'height': 128, 'size': 2.90, 'colors': 'BWRY'},
            {'model_number': 'ET0290-3D', 'width': 296, 'height': 128, 'size': 2.90, 'colors': 'BWR'},
            {'model_number': 'ET0290-3F', 'width': 296, 'height': 128, 'size': 2.90, 'colors': 'BW'},
            {'model_number': 'ET0290-54', 'width': 296, 'height': 128, 'size': 2.90, 'colors': 'BW'},
        ]
        
        for spec in specs:
            TagHardware.objects.get_or_create(
                model_number=spec['model_number'],
                defaults={
                    'width_px': spec['width'],
                    'height_px': spec['height'],
                    'display_size_inch': Decimal(str(spec['size'])),
                    'color_scheme': spec['colors']
                }
            )
        self.stdout.write("Hardware Catalog seeded.")

        # 2. SEED ROLES & PERMISSIONS
        # Configures the SAIS RBAC (Role-Based Access Control) matrix.
        role_permissions = {
            'Owner': {
                Company: ['change', 'view'],
                Store: ['change', 'view'],
                TagHardware: ['view'],
                Gateway: ['change', 'view'],
                Product: ['add', 'change', 'delete', 'view'],
                ESLTag: ['add', 'change', 'delete', 'view'],
                User: ['add', 'change', 'delete', 'view'],
            },
            'Store Manager': {
                Company: ['view'],
                Store: ['view'],
                TagHardware: ['view'],
                Gateway: ['change', 'view'],
                Product: ['add', 'change', 'delete', 'view'],
                ESLTag: ['add', 'change', 'delete', 'view'],
                User: ['add', 'change', 'delete', 'view'],
            },
            'Store Staff': {
                Company: ['view'],
                Store: ['view'],
                TagHardware: ['view'],
                Gateway: ['view'],
                Product: ['add', 'change', 'delete', 'view'],
                ESLTag: ['add', 'change', 'delete', 'view'],
                User: ['view'],
            },
            'Read Only': {
                Company: ['view'],
                Store: ['view'],
                TagHardware: ['view'],
                Gateway: ['view'],
                Product: ['view'],
                ESLTag: ['view'],
                User: ['view'],
            }
        }

        # Extra technical permissions for all roles
        monitoring_perms_config = [
            {'app': 'django_celery_results', 'model': 'taskresult', 'action': 'view'},
            {'app': 'auth', 'model': 'group', 'action': 'view'},
        ]

        for role_name, config in role_permissions.items():
            group, _ = Group.objects.get_or_create(name=role_name)
            perms_to_set = []
            
            # Map simplified names (change, view) to Django codenames (change_company)
            for model, actions in config.items():
                ct = ContentType.objects.get_for_model(model)
                for action in actions:
                    codename = f"{action}_{model._meta.model_name}"
                    try:
                        perm = Permission.objects.get(content_type=ct, codename=codename)
                        perms_to_set.append(perm)
                    except Permission.DoesNotExist:
                        continue
            
            # Add the Monitoring access
            for m_perm in monitoring_perms_config:
                try:
                    ct = ContentType.objects.get(app_label=m_perm['app'], model=m_perm['model'])
                    codename = f"{m_perm['action']}_{m_perm['model']}"
                    perm = Permission.objects.get(content_type=ct, codename=codename)
                    perms_to_set.append(perm)
                except (ContentType.DoesNotExist, Permission.DoesNotExist):
                    pass

            group.permissions.set(perms_to_set)
            self.stdout.write(f"Permissions applied to {role_name}")

        # 3. BASE DATA SETUP
        company, _ = Company.objects.get_or_create(
            name="Admin Company", 
            defaults={'is_active': True, 'contact_email': 'info@sais.com'}
        )
        store, _ = Store.objects.get_or_create(
            name="Admin Store", 
            company=company, 
            defaults={'is_active': True, 'location_code': 'MB-01'}
        )

        # 4. INITIAL ADMIN ACCOUNTS
        if not User.objects.filter(username='admin').exists():
            User.objects.create_superuser('admin', 'admin@sais.com', 'admin123', company=company)
            self.stdout.write(self.style.SUCCESS("Admin created: admin / admin123"))

        if not User.objects.filter(username='owner_test').exists():
            u = User.objects.create_user('owner_test', 'owner@sais.com', 'owner123', company=company, role='owner')
            u.groups.add(Group.objects.get(name='Owner'))
            u.is_staff = True 
            u.save()
            self.stdout.write(self.style.SUCCESS("Test Owner created: owner_test / owner123"))

        # 5. DUMMY HARDWARE
        Gateway.objects.get_or_create(
            gateway_mac="testGateway",
            defaults={'store': store}
        )

        # 6. GLOBAL SYSTEM SETTINGS
        settings_to_seed = [
            {'key': 'ESL_ENCRYPTION_KEY', 'value': 'FFFFFFFFFFFFFFFF', 'description': '8-byte encryption key (16-digit hex)'},
            {'key': 'DEFAULT_HEARTBEAT_INTERVAL', 'value': '300', 'description': 'Default heartbeat interval in seconds if not provided by gateway'},
            {'key': 'OFFLINE_TIMEOUT_MULTIPLIER', 'value': '4', 'description': 'Multiply heartbeat interval by this to determine offline status'},
            {'key': 'LOG_RETENTION_DAYS', 'value': '15', 'description': 'Number of days to keep MQTT communication logs'},
            {'key': 'ESL_SEND_DELAY_MS', 'value': '500', 'description': 'Delay in milliseconds between sending individual tags to a gateway'},
            {'key': 'DEFAULT_GATEWAY_SERVER', 'value': '192.168.1.92:9081', 'description': 'Default server address (IP:Port) for hardware configuration'},
        ]
        for s in settings_to_seed:
            GlobalSetting.objects.get_or_create(key=s['key'], defaults={'value': s['value'], 'description': s['description']})

        self.stdout.write(self.style.SUCCESS('Successfully seeded all base data.'))
