import os
from celery import Celery

"""
CELERY CONFIGURATION: BACKGROUND TASK RUNTIME
---------------------------------------------
This file initializes Celery for the SAIS project.
It tells Celery how to find its configuration and where to look for tasks.
"""

# 1. Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'esl_cloud.settings')

app = Celery('esl_cloud')

# 2. Configures Celery using variables from settings.py that start with 'CELERY_'.
# Namespace 'CELERY' means it looks for CELERY_BROKER_URL, etc.
app.config_from_object('django.conf:settings', namespace='CELERY')

# 3. Automatically looks for a 'tasks.py' file inside every installed app
# (e.g., core/tasks.py).
app.autodiscover_tasks()
