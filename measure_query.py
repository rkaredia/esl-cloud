import os
import django
from django.db import connection
from django.test.utils import CaptureQueriesContext

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'esl_cloud.settings')
django.setup()

from core.models import ESLTag, Store, Company, Gateway

# Setup
company = Company.objects.create(name="Test")
store = Store.objects.create(name="Store", company=company)
gw = Gateway.objects.create(store=store, estation_id="G1", gateway_mac="M1")
tag = ESLTag.objects.create(store=store, tag_mac="T1", gateway=gw)

print("--- Without select_related ---")
with CaptureQueriesContext(connection) as ctx:
    t = ESLTag.objects.get(pk=tag.pk)
    _ = t.gateway.is_currently_online()
print(f"Queries: {len(ctx)}")

print("--- With select_related ---")
with CaptureQueriesContext(connection) as ctx:
    t = ESLTag.objects.select_related('gateway').get(pk=tag.pk)
    _ = t.gateway.is_currently_online()
print(f"Queries: {len(ctx)}")
