import os
import django
from django.db.models import Case, When, Value, IntegerField

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'esl_cloud.settings')
django.setup()

from core.models import ESLTag

qs = ESLTag.objects.annotate(
    image_sort_val=Case(
        When(paired_product__isnull=True, then=Value(2)),
        When(tag_image__isnull=False, tag_image__gt='', then=Value(0)),
        default=Value(1),
        output_field=IntegerField(),
    )
).order_by('image_sort_val', '-updated_at')

print(f"{'MAC':<15} | {'Product':<20} | {'Image':<20} | {'SortVal'}")
print("-" * 70)
for t in qs:
    prod = str(t.paired_product)[:20] if t.paired_product else "None"
    img = os.path.basename(t.tag_image.name)[:20] if t.tag_image else "None"
    print(f"{t.tag_mac:<15} | {prod:<20} | {img:<20} | {t.image_sort_val}")
