import os
import django
from django.db.models import Q

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'esl_cloud.settings')
django.setup()

from core.models import ESLTag, Product

# Check for tags with paired_product_id that doesn't exist in Product table
tags = ESLTag.objects.exclude(paired_product_id__isnull=True)
orphans = []
for t in tags:
    if not Product.objects.filter(id=t.paired_product_id).exists():
        orphans.append(t)

print(f"Orphaned tags (ID exists but Product doesn't): {len(orphans)}")
for t in orphans:
    print(f"Tag: {t.tag_mac}, Product ID: {t.paired_product_id}")

# Check for tags where paired_product is None but tag_image is not empty
stale_images = ESLTag.objects.filter(paired_product__isnull=True).exclude(tag_image='').exclude(tag_image__isnull=True)
print(f"Tags with no product but having image: {stale_images.count()}")
for t in stale_images:
    print(f"Tag: {t.tag_mac}, Image: {t.tag_image}")

# Check the actual values of image_sort_val for all tags
from django.db.models import Case, When, Value, IntegerField
qs = ESLTag.objects.annotate(
    image_sort_val=Case(
        When(paired_product__isnull=True, then=Value(2)),
        When(tag_image__isnull=False, tag_image__gt='', then=Value(0)),
        default=Value(1),
        output_field=IntegerField(),
    )
).order_by('image_sort_val', '-updated_at')

print("\nAll Tags Sorting:")
for t in qs:
    prod_exists = "YES" if t.paired_product_id else "NO"
    img_exists = "YES" if t.tag_image else "NO"
    print(f"MAC: {t.tag_mac} | Prod: {prod_exists} | Img: {img_exists} | SortVal: {t.image_sort_val}")
