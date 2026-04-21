from django.db import migrations

def normalize_macs(apps, schema_editor):
    ESLTag = apps.get_model('core', 'ESLTag')
    import re
    for tag in ESLTag.objects.all():
        if tag.tag_mac:
            cleaned = re.sub(r'[^0-9A-Za-z]', '', str(tag.tag_mac)).strip().upper()
            if tag.tag_mac != cleaned:
                tag.tag_mac = cleaned
                tag.save()

class Migration(migrations.Migration):
    dependencies = [
        ('core', '0046_alter_esltag_tag_mac'),
    ]
    operations = [
        migrations.RunPython(normalize_macs, reverse_code=migrations.RunPython.noop),
    ]
