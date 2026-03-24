import os
import django
import uuid
from django.db.models import Case, When, Value, IntegerField
from unittest.mock import patch

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'esl_cloud.settings')
django.setup()

from core.models import ESLTag, Product, Store, TagHardware

store = Store.objects.first()
spec = TagHardware.objects.first()

with patch('core.tasks.update_tag_image_task.delay'):
    # 1. No Product
    t1 = ESLTag.objects.create(store=store, tag_mac=f"MAC_{uuid.uuid4().hex[:6]}", hardware_spec=spec)

    # 2. Pending (Has product but no image)
    p1 = Product.objects.create(store=store, sku=f"SKU_{uuid.uuid4().hex[:6]}", name="P1", price=10.0)
    t2 = ESLTag.objects.create(store=store, tag_mac=f"MAC_{uuid.uuid4().hex[:6]}", paired_product=p1, hardware_spec=spec)
    ESLTag.objects.filter(id=t2.id).update(tag_image='')

    # 3. Generated (Has product and image)
    p2 = Product.objects.create(store=store, sku=f"SKU_{uuid.uuid4().hex[:6]}", name="P2", price=20.0)
    t3 = ESLTag.objects.create(store=store, tag_mac=f"MAC_{uuid.uuid4().hex[:6]}", paired_product=p2, hardware_spec=spec)
    ESLTag.objects.filter(id=t3.id).update(tag_image='some_image.bmp')

# Query with annotation
qs = ESLTag.objects.annotate(
    image_sort_val=Case(
        When(paired_product__isnull=True, then=Value(2)),
        When(tag_image__isnull=False, tag_image__gt='', then=Value(0)),
        default=Value(1),
        output_field=IntegerField(),
    )
).order_by('image_sort_val', '-updated_at')

print("EXPECTED ORDER: MAC3 (Generated, 0), MAC2 (Pending, 1), MAC1 (No Product, 2)")
target_macs = [t1.tag_mac, t2.tag_mac, t3.tag_mac]
for t in qs:
    if t.tag_mac in target_macs:
        status = "NO PRODUCT" if not t.paired_product_id else ("GENERATED" if t.tag_image else "PENDING")
        print(f"MAC: {t.tag_mac}, Status: {status}, Sort Val: {t.image_sort_val}")

# Cleanup
t1.delete()
t2.delete()
t3.delete()
p1.delete()
p2.delete()
