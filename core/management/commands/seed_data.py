from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from core.models import User, Company, Store, TagHardware, Gateway, ESLTag, Product
from decimal import Decimal

class Command(BaseCommand):
    help = "Seeds hardware specs, roles, and base company data."

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

        for role_name, config in role_permissions.items():
            group, _ = Group.objects.get_or_create(name=role_name)
            perms_to_set = []
            for model, actions in config.items():
                ct = ContentType.objects.get_for_model(model)
                for action in actions:
                    codename = f"{action}_{model._meta.model_name}"
                    try:
                        perm = Permission.objects.get(content_type=ct, codename=codename)
                        perms_to_set.append(perm)
                    except Permission.DoesNotExist:
                        continue
            group.permissions.set(perms_to_set)
            self.stdout.write(f"Permissions applied to {role_name}")

        # 3. Base Setup (Company/Store)
        company, _ = Company.objects.get_or_create(
            name="SAIS Global", 
            defaults={'is_active': True, 'contact_email': 'info@sais.com'}
        )
        store, _ = Store.objects.get_or_create(
            name="Main Branch", 
            company=company, 
            defaults={'is_active': True, 'location_code': 'MB-01'}
        )

        # 4. Create Initial Admin
        if not User.objects.filter(username='admin').exists():
            User.objects.create_superuser('admin', 'admin@sais.com', 'admin123', company=company)
            self.stdout.write(self.style.SUCCESS("Admin created: admin / admin123"))
        else:
            # Ensure staff flag is set if user exists but can't login
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
            gateway_mac="00:1A:2B:3C:4D:5E",
            defaults={'store': store, 'is_active': True}
        )

        self.stdout.write(self.style.SUCCESS('Successfully seeded all base data.'))