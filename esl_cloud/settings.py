"""
SAIS SYSTEM SETTINGS (THE ENGINE CONFIG)
----------------------------------------
This file contains the configuration for the entire SAIS platform.
Django uses these settings to connect to the database, secure the website,
and communicate with background workers.

If you are coming from a Data Warehouse background, think of this as
your 'Configuration Schema' or 'Environment Variables'.
"""

import os
import environ
from pathlib import Path
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from django.utils import timezone

# BASE_DIR: The root folder of the project. Used to find certificates and logs.
BASE_DIR = Path(__file__).resolve().parent.parent

# django-environ: A library that reads secret values from a '.env' file
# so they aren't hardcoded in the script (Crucial for Security).
environ.Env.read_env(os.path.join(BASE_DIR, '.env'))

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
)

# =================================================================
# 1. SECURITY SETTINGS
# =================================================================

# SECRET_KEY: Used for cryptographic signing (Sessions, Passwords).
# MUST be kept secret!
SECRET_KEY = env('SECRET_KEY')

# DEBUG: Set to True for development, False for production.
# When False, Django won't show detailed error pages to users.
DEBUG = env('DEBUG')

# ALLOWED_HOSTS: The domain names that this server is allowed to serve.
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1'])

# CSRF & COOKIE SECURITY:
# Protects users from 'Cross-Site Request Forgery' attacks.
CSRF_COOKIE_SECURE = not DEBUG  # Only send cookies over HTTPS
CSRF_COOKIE_HTTPONLY = True     # Prevents JavaScript from stealing session cookies
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True

# SECURITY HEADERS:
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY' # Anti-Clickjacking (Prevents site from being in an <iframe>)

# =================================================================
# 2. LOCALIZATION & TIME (Texas-based)
# =================================================================

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'America/Chicago'
USE_I18N = True # Internationalization (Translations)
USE_TZ = True   # Store all dates in UTC in the DB, but show them in local time in UI
CELERY_TIMEZONE = 'US/Central'

# Log Filename Logic: Creates a new log file every day (e.g., SAIS_log_20231027.log)
texas_tz = ZoneInfo('US/Central')
LOG_FILENAME = f"SAIS_log_{datetime.now(texas_tz).strftime('%Y%m%d')}.log"
LOG_PATH = os.path.join(BASE_DIR, 'logs', LOG_FILENAME)

class LocalTimeFormatter(logging.Formatter):
    """Custom formatter to show log timestamps in local Texas time."""
    def formatTime(self, record, datefmt=None):
        dt = timezone.localtime(timezone.now())
        return dt.strftime('%Y-%m-%d %H:%M:%S')

# =================================================================
# 3. DATABASE CONFIGURATION
# =================================================================

DATABASES = {
    # Reads 'DATABASE_URL' from .env.
    # Example: postgres://user:pass@localhost:5432/dbname
    'default': env.db('DATABASE_URL', default=f'sqlite:///{BASE_DIR}/db.sqlite3')
}

# =================================================================
# 4. INSTALLED APPS & MIDDLEWARE
# =================================================================

INSTALLED_APPS = [
    # Core Django apps
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party extensions
    'django_celery_results', # Saves background task logs to the DB

    # SAIS Custom modules
    'core',        # The heart of the ESL system
    'help_module', # User guides and documentation
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',

    # SAIS Custom Middleware
    'core.middleware.StoreContextMiddleware',   # Manages active store context
    'core.middleware.SecurityHeadersMiddleware',# Adds security headers to responses
]

ROOT_URLCONF = 'esl_cloud.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'], # Look for custom HTML files in the /templates folder
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.store_context', # Injects store data into templates
            ],
        },
    },
]

# =================================================================
# 5. USER AUTHENTICATION
# =================================================================

# Link to our custom User model in core/models.py
AUTH_USER_MODEL = 'core.User'

LOGOUT_REDIRECT_URL = '/admin/login/'
LOGIN_REDIRECT_URL = '/admin/'

# Password Strength Rules
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 10}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# =================================================================
# 6. STATIC & MEDIA (Files)
# =================================================================

# STATIC: Files like CSS, JS, and Logos that don't change.
STATIC_URL = '/static/'
STATICFILES_DIRS = [os.path.join(BASE_DIR, "static")]
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# MEDIA: Files uploaded by users (like the generated Tag BMP images).
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# =================================================================
# 7. CELERY / REDIS (Background Workers)
# =================================================================

# Redis is the 'Message Broker' that holds tasks until a worker is ready.
REDIS_URL = env('REDIS_URL', default='redis://redis:6379/0')

# CACHE CONFIGURATION (Using Redis for Cross-Process Locking)
# -----------------------------------------------------------
# We use Redis as a shared cache so that Locks and Debouncing work
# correctly across Web, Celery Worker, and MQTT Worker processes.
# For local tests without a running Redis, we fallback to Local Memory.
import sys
if 'test' in sys.argv:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_URL,
        }
    }

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = 'django-db' # Save success/failure status to the DB
CELERY_TRACK_STARTED = True
CELERY_RESULT_EXTENDED = True
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_RESULT_EXPIRES = 259200  # Results expire after 3 days to save space

from celery.schedules import crontab

# CELERY BEAT: The 'Task Scheduler' (like Windows Task Scheduler or Cron).
CELERY_BEAT_SCHEDULE = {
    # Every minute: Mark gateways offline if they haven't sent a heartbeat
    'check-gateways-status-every-minute': {
        'task': 'core.tasks.check_gateways_status_task',
        'schedule': crontab(minute='*'),
    },
    # Daily at midnight: Purge old logs from the database and disk
    'cleanup-old-logs-daily': {
        'task': 'core.tasks.cleanup_old_logs_task',
        'schedule': crontab(hour=0, minute=0),
    },
    # Every minute: Report Celery worker heartbeat
    'celery-worker-heartbeat': {
        'task': 'core.tasks.report_service_status_task',
        'schedule': crontab(minute='*'),
    },
}

# =================================================================
# 8. MQTT (Hardware Communication)
# =================================================================

MQTT_SERVER = env('MQTT_SERVER', default='mqtt_broker')
MQTT_PORT = env.int('MQTT_PORT', default=9081)
MQTT_USER = env('MQTT_USER', default='test')
MQTT_PASS = env('MQTT_PASS', default='123456')
MQTT_TOPIC = "gw/+/status"

# =================================================================
# 9. LOGGING CONFIGURATION
# =================================================================

LOGS_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOGS_DIR, exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'sais_formatter': {
            '()': LocalTimeFormatter,
            'format': '[{levelname}] {asctime} [{module}.{funcName}:{lineno}] - {process:d} {thread:d} - {message}',
            'style': '{',
        },
    },
    'handlers': {
        'file': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': LOG_PATH,
            'formatter': 'sais_formatter',
            'encoding': 'utf-8',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['file'],
            'level': 'INFO',
            'propagate': True,
        },
        'core': {
            'handlers': ['file'],
            'level': 'DEBUG',
            'propagate': True,
        },
    },
}
