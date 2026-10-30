"""
Django settings for ASK Company.

Configuration comes from environment variables so the same code runs in
development (SQLite) and production (PostgreSQL). See backend/.env.example.
"""
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    # A key left blank in .env (e.g. "SUPABASE_URL=") counts as not set.
    return os.environ.get(name) or default


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


# Load a local .env file if present (simple KEY=VALUE lines, no extra dependency).
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

# Render sets RENDER=true, so debug stays off there even if DJANGO_DEBUG is forgotten.
DEBUG = env_bool("DJANGO_DEBUG", not env("RENDER"))
SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me-in-production")
if not DEBUG and SECRET_KEY.startswith("dev-only-"):
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY to a long random string in production.")
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
# Render gives each web service its own hostname, e.g. asmah-backend.onrender.com.
if env("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(env("RENDER_EXTERNAL_HOSTNAME"))

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "corsheaders",
    "accounts",
    "lending",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "asmah.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "asmah.wsgi.application"

# Database: PostgreSQL when DATABASE_URL (as Render provides) or POSTGRES_DB is set,
# otherwise SQLite.
if env("DATABASE_URL"):
    url = urlsplit(env("DATABASE_URL"))
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(url.path.lstrip("/")),
            "USER": unquote(url.username or ""),
            "PASSWORD": unquote(url.password or ""),
            "HOST": url.hostname or "",
            "PORT": str(url.port or 5432),
            # Options in the URL, such as ?sslmode=require, go straight to the driver.
            "OPTIONS": dict(parse_qsl(url.query)),
            "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "60")),
            "CONN_HEALTH_CHECKS": True,
        }
    }
elif env("POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB"),
            "USER": env("POSTGRES_USER", "postgres"),
            "PASSWORD": env("POSTGRES_PASSWORD", ""),
            "HOST": env("POSTGRES_HOST", "localhost"),
            "PORT": env("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            # Wait for locks instead of failing, and take write locks up front.
            "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"},
        }
    }

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "Africa/Dar_es_Salaam")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # WhiteNoise serves the admin's CSS and JS in production after `collectstatic`.
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# Borrower photos and the logo go to Supabase Storage when it is set up, because a host
# like Render wipes its own disk on every deploy. The bucket must be public so the
# images load in the browser without signing in to Supabase.
if env("SUPABASE_URL"):
    from botocore.config import Config

    # Accept the project URL or the S3 endpoint the dashboard shows (…/storage/v1/s3).
    _supabase = env("SUPABASE_URL").rstrip("/").removesuffix("/storage/v1/s3")
    _bucket = env("SUPABASE_BUCKET", "media")
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": _bucket,
            "endpoint_url": f"{_supabase}/storage/v1/s3",
            "region_name": env("SUPABASE_S3_REGION", "us-east-1"),
            "access_key": env("SUPABASE_S3_ACCESS_KEY_ID"),
            "secret_key": env("SUPABASE_S3_SECRET_ACCESS_KEY"),
            # Image links use the bucket's public address, so they never expire.
            "custom_domain": f"{urlsplit(_supabase).netloc}/storage/v1/object/public/{_bucket}",
            "querystring_auth": False,
            # Two uploads with the same file name get different names instead of replacing each other.
            "file_overwrite": False,
            "client_config": Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                # Only send the checksums S3 itself requires; not every S3-compatible
                # service accepts the extra ones newer boto3 versions add by default.
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        },
    }
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
BACKUP_DIR = Path(env("BACKUP_DIR", str(BASE_DIR / "backups")))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["accounts.permissions.IsSuperAdmin"],
    "DEFAULT_PAGINATION_CLASS": "lending.pagination.Pagination",
    "PAGE_SIZE": 25,
    "COERCE_DECIMAL_TO_STRING": False,
    "DEFAULT_THROTTLE_RATES": {"login": "10/min", "password_reset": "5/hour"},
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=int(env("JWT_ACCESS_HOURS", "8"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(env("JWT_REFRESH_DAYS", "7"))),
    "ROTATE_REFRESH_TOKENS": True,
    "UPDATE_LAST_LOGIN": True,
}

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")

# Email is used for "forgot password". In development emails print to the console.
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "ASK Company <no-reply@askcompany.local>")
FRONTEND_URL = env("FRONTEND_URL", "http://localhost:3000")

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Print warnings and errors (with tracebacks) to the console so they show in the host's logs.
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {"console": {"class": "logging.StreamHandler", "level": "WARNING"}},
        "root": {"handlers": ["console"], "level": "WARNING"},
    }
