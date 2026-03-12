
import os
import django
from unittest.mock import patch

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'esl_cloud.settings')
os.environ.setdefault('SECRET_KEY', 'dev')
django.setup()

from django.db import connection
from django.test import RequestFactory
from django.contrib.auth import get_user_model
from core.models import Company, Store, ESLTag, TagHardware, Product, Gateway

def measure_admin_queries():
    # Mocking celery delay to avoid Redis connection errors during setup
    with patch('core.tasks.update_tag_image_task.delay'):
        User = get_user_model()
        company, _ = Company.objects.get_or_create(name="Benchmark Co")
        store, _ = Store.objects.get_or_create(name="Benchmark Store", company=company)
        user, _ = User.objects.get_or_create(username='bench', defaults={'is_superuser': True, 'is_staff': True})
        if _:
            user.set_password('pass')
            user.save()

        hw, _ = TagHardware.objects.get_or_create(model_number="B1", defaults={'width_px': 200, 'height_px': 100, 'display_size_inch': 2.1})
        gw, _ = Gateway.objects.get_or_create(estation_id="B001", defaults={'gateway_mac': "AA:BB:CC:DD:EE:01", 'store': store})

        # Create 20 tags with products
        ESLTag.objects.filter(store=store).delete()
        Product.objects.filter(store=store).delete()

        for i in range(20):
            p = Product.objects.create(sku=f"P{i}", name=f"Product {i}", price=10.0, store=store)
            ESLTag.objects.create(tag_mac=f"MAC{i}", store=store, paired_product=p, hardware_spec=hw, gateway=gw)

    from core.admin.hardware import ESLTagAdmin
    from core.admin.inventory import ProductAdmin
    from core.admin.base import admin_site

    factory = RequestFactory()

    # Enable query logging
    from django.test.utils import CaptureQueriesContext

    print("--- Optimized ESLTagAdmin Benchmark ---")
    admin_tag = ESLTagAdmin(ESLTag, admin_site)
    request_tag = factory.get('/admin/core/esltag/')
    request_tag.user = user
    request_tag.active_store = store

    with CaptureQueriesContext(connection) as ctx:
        cl = admin_tag.get_changelist_instance(request_tag)
        results = list(cl.result_list)
        for obj in results:
            for field in admin_tag.list_display:
                if hasattr(admin_tag, field):
                    getattr(admin_tag, field)(obj)

    print(f"Query count for 20 tags (Optimized): {len(ctx)}")

    print("\n--- Optimized ProductAdmin Benchmark ---")
    admin_prod = ProductAdmin(Product, admin_site)
    request_prod = factory.get('/admin/core/product/')
    request_prod.user = user
    request_prod.active_store = store

    with CaptureQueriesContext(connection) as ctx2:
        cl = admin_prod.get_changelist_instance(request_prod)
        results = list(cl.result_list)
        for obj in results:
             for field in admin_prod.list_display:
                if hasattr(admin_prod, field):
                    getattr(admin_prod, field)(obj)

    print(f"Query count for 20 products (Optimized): {len(ctx2)}")

if __name__ == "__main__":
    measure_admin_queries()
