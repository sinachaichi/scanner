import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=BASE_DIR / '.env')

# ── Required credentials ─────────────────────────────────────────────────────
# The app refuses to start if these are absent. Never hardcode fallbacks here.
_REQUIRED = ('SECRET_KEY', 'FIELD_ENCRYPTION_KEY')
_missing = [v for v in _REQUIRED if not os.getenv(v)]
if _missing:
    print(
        f"ERROR: Required environment variable(s) not set: {', '.join(_missing)}\n"
        "       Copy .env.example → .env and fill in the values.",
        file=sys.stderr,
    )
    sys.exit(1)

SECRET_KEY = os.environ['SECRET_KEY']
FIELD_ENCRYPTION_KEY = os.environ['FIELD_ENCRYPTION_KEY']

# ── Core settings ─────────────────────────────────────────────────────────────
DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 'yes')

_raw_hosts = os.getenv('ALLOWED_HOSTS', 'localhost')
ALLOWED_HOSTS = [h.strip() for h in _raw_hosts.split(',') if h.strip()]

# ── Installed apps ────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'django_celery_results',
    'django_celery_beat',
    'scanner',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# ── Database ──────────────────────────────────────────────────────────────────
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# ── Auth ──────────────────────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ── i18n ──────────────────────────────────────────────────────────────────────
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# ── Static files (served by WhiteNoise — no Nginx needed) ────────────────────
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── CSRF ──────────────────────────────────────────────────────────────────────
_port = os.getenv('RANDOM_PORT', '8000')
CSRF_TRUSTED_ORIGINS = [
    f'http://{host}:{_port}'
    for host in ALLOWED_HOSTS
    if host != '*'
]

# ── Celery ────────────────────────────────────────────────────────────────────
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = 'django-db'
CELERY_RESULT_EXPIRES = 60 * 60 * 24  # 1 day
CELERY_TASK_DEFAULT_QUEUE = 'scanner_queue'
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'
DEFAULT_CELERY_QUEUE = CELERY_TASK_DEFAULT_QUEUE

# ── Telegram / xray ──────────────────────────────────────────────────────────
TELEGRAM_API_ID = os.getenv('TELEGRAM_API_ID')
TELEGRAM_API_HASH = os.getenv('TELEGRAM_API_HASH')
XRAY_PATH = os.getenv('XRAY_PATH', './xray')
MAX_CONCURRENT_XRAY = int(os.getenv('MAX_CONCURRENT_XRAY', '5'))

# ── Logging ───────────────────────────────────────────────────────────────────
_log_level = os.getenv('LOG_LEVEL', 'INFO').upper()

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{asctime} {levelname} {name} {message}',
            'style': '{',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'scanner': {
            'handlers': ['console'],
            'level': _log_level,
            'propagate': False,
        },
        'task': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
