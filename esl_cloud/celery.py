import os
from celery import Celery

# 1. Change 'your_project_name' to 'esl_cloud'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'esl_cloud.settings')

app = Celery('esl_cloud')

# 2. This links your Django settings.py to Celery
app.config_from_object('django.conf:settings', namespace='CELERY')

# 3. This tells Celery to look for tasks.py in your apps (like 'core')
app.autodiscover_tasks()