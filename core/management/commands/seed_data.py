from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from core.models import User, Company, Store, TagHardware, Gateway, ESLTag, Product
from decimal import Decimal

class Command(BaseCommand):
    help = "Seeds hardware specs, roles, base company data, and monitoring permissions."

    def handle(self, *args, **options):
        # 1. Seed Global Hardware Catalog (TagHardware)
        specs = [
            {'model_number': 'ET0213-85', 'width': 250, 'height': 122, 'size': 2.13, 'colors': 'BWR'},
            {'model_number': 'ET0290-85', 'width': 296, 'height': 128, 'size': 2.90, 'colors': 'BWRY'},
            {'model_number': 'ET0420-85', 'width': 400, 'height': 300, 'size': 4.20, 'colors': 'BWRY'},
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

        # 2. Define and Seed Permissions
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

        # 2b. Monitoring and Group View permissions (Applied to ALL roles)
        monitoring_perms_config = [
            {'app': 'django_celery_results', 'model': 'taskresult', 'action': 'view'},
            {'app': 'auth', 'model': 'group', 'action': 'view'},
        ]

        for role_name, config in role_permissions.items():
            group, _ = Group.objects.get_or_create(name=role_name)
            perms_to_set = []
            
            # Add Standard Model Permissions
            for model, actions in config.items():
                ct = ContentType.objects.get_for_model(model)
                for action in actions:
                    codename = f"{action}_{model._meta.model_name}"
                    try:
                        perm = Permission.objects.get(content_type=ct, codename=codename)
                        perms_to_set.append(perm)
                    except Permission.DoesNotExist:
                        continue
            
            # Add System Monitoring Permissions
            for m_perm in monitoring_perms_config:
                try:
                    ct = ContentType.objects.get(app_label=m_perm['app'], model=m_perm['model'])
                    codename = f"{m_perm['action']}_{m_perm['model']}"
                    perm = Permission.objects.get(content_type=ct, codename=codename)
                    perms_to_set.append(perm)
                except (ContentType.DoesNotExist, Permission.DoesNotExist):
                    self.stdout.write(self.style.WARNING(f"Monitoring perm {m_perm['model']} not found."))

            group.permissions.set(perms_to_set)
            self.stdout.write(f"Permissions (including Monitoring) applied to {role_name}")

        # 3. Base Setup (Company/Store)
        company, _ = Company.objects.get_or_create(
            name="Admin Company", 
            defaults={'is_active': True, 'contact_email': 'info@sais.com'}
        )
        store, _ = Store.objects.get_or_create(
            name="Admin Store", 
            company=company, 
            defaults={'is_active': True, 'location_code': 'MB-01'}
        )

        # 4. Create Initial Admin
        if not User.objects.filter(username='admin').exists():
            User.objects.create_superuser('admin', 'admin@sais.com', 'admin123', company=company)
            self.stdout.write(self.style.SUCCESS("Admin created: admin / admin123"))
        else:
            u = User.objects.get(username='admin')
            u.is_staff = True
            u.save()

        # 5. Create a Test Owner
        if not User.objects.filter(username='owner_test').exists():
            u = User.objects.create_user('owner_test', 'owner@sais.com', 'owner123', company=company, role='owner')
            u.groups.add(Group.objects.get(name='Owner'))
            u.is_staff = True 
            u.save()
            self.stdout.write(self.style.SUCCESS("Test Owner created: owner_test / owner123"))

        # 6. Pre-populate Test Gateway
        Gateway.objects.get_or_create(
            gateway_mac="testGateway",
            defaults={'store': store, 'is_active': True}
        )

        self.stdout.write(self.style.SUCCESS('Successfully seeded all base data with monitoring access.'))