import os
from celery import Celery

# This is where the error is likely coming from.
# It MUST point to the folder containing your 'settings.py'.
# If your settings.py is in /SAIS_platform/SAIS_platform/settings.py, use 'SAIS_platform.settings'
# If your settings.py is in /SAIS_platform/core/settings.py, use 'core.settings'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'esl_cloud.settings')

app = Celery('core')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()