"""
Django settings for the Cloud-Based Shipment Records Management System (CBSRMS).

Everything environment-specific is read from environment variables (or a .env
file in the project root), so the same code runs on a laptop (SQLite) and on a
cloud host such as Render (PostgreSQL).
"""
import os
from pathlib import Path
from urllib.parse import urlparse, unquote

from django.contrib.messages import constants as message_constants
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# Optional: load variables from a .env file if python-dotenv is installed.
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:  # pragma: no cover
    pass


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return str(os.environ.get(name, default)).strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------
DEBUG = env_bool("DJANGO_DEBUG", True)

_DEV_KEY = "dev-only-insecure-key-change-me-before-deploying"
SECRET_KEY = env("DJANGO_SECRET_KEY", _DEV_KEY)
if not DEBUG and SECRET_KEY == _DEV_KEY:
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY when DJANGO_DEBUG is off.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
_render_host = env("RENDER_EXTERNAL_HOSTNAME")  # set automatically by Render
if _render_host:
    ALLOWED_HOSTS.append(_render_host)
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
if _render_host:
    CSRF_TRUSTED_ORIGINS.append(f"https://{_render_host}")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Project apps
    "accounts.apps.AccountsConfig",
    "shipments.apps.ShipmentsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Remembers the acting user + IP so the audit trail can attribute changes.
    "shipments.middleware.CurrentUserMiddleware",
]

# WhiteNoise serves static files in production (used only if installed).
try:
    import whitenoise  # noqa: F401

    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
    _STATIC_BACKEND = "whitenoise.storage.CompressedStaticFilesStorage"
except ImportError:  # pragma: no cover
    _STATIC_BACKEND = "django.contrib.staticfiles.storage.StaticFilesStorage"

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "shipments.context_processors.cbsrms",
            ],
        },
    },
]

# --------------------------------------------------------------------------
# Database  (SQLite by default; PostgreSQL / MySQL via env vars)
# --------------------------------------------------------------------------


def _database_from_url(url):
    parsed = urlparse(url)
    scheme = parsed.scheme
    if scheme in ("postgres", "postgresql"):
        engine = "django.db.backends.postgresql"
    elif scheme in ("mysql",):
        engine = "django.db.backends.mysql"
    elif scheme == "sqlite":
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": parsed.path.lstrip("/") or BASE_DIR / "db.sqlite3"}
    else:
        raise ImproperlyConfigured(f"Unsupported DATABASE_URL scheme: {scheme}")
    cfg = {
        "ENGINE": engine,
        "NAME": parsed.path.lstrip("/"),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or ""),
        "CONN_MAX_AGE": 60,
    }
    if engine.endswith("mysql"):
        cfg["OPTIONS"] = {"charset": "utf8mb4"}
    return cfg


_db_engine = env("DB_ENGINE", "sqlite").lower()
if env("DATABASE_URL"):
    DATABASES = {"default": _database_from_url(env("DATABASE_URL"))}
elif _db_engine in ("postgres", "postgresql"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DB_NAME", "cbsrms"),
            "USER": env("DB_USER", "postgres"),
            "PASSWORD": env("DB_PASSWORD", ""),
            "HOST": env("DB_HOST", "localhost"),
            "PORT": env("DB_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }
elif _db_engine == "mysql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": env("DB_NAME", "cbsrms"),
            "USER": env("DB_USER", "root"),
            "PASSWORD": env("DB_PASSWORD", ""),
            "HOST": env("DB_HOST", "localhost"),
            "PORT": env("DB_PORT", "3306"),
            "OPTIONS": {"charset": "utf8mb4"},
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"
SESSION_COOKIE_AGE = 60 * 60 * 8  # 8 hours
SESSION_SAVE_EVERY_REQUEST = False

# --------------------------------------------------------------------------
# Internationalisation
# --------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("DJANGO_TIME_ZONE", "Africa/Nairobi")
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------
# Static & media files
# --------------------------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", BASE_DIR / "media"))

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": _STATIC_BACKEND},
}

# Reject very large request bodies early (document uploads are limited below).
DATA_UPLOAD_MAX_MEMORY_SIZE = 15 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# --------------------------------------------------------------------------
# E-mail (notifications). Console backend by default: messages are printed in
# the terminal. Set EMAIL_HOST etc. in .env to send real e-mail.
# --------------------------------------------------------------------------
if env("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = env("EMAIL_HOST")
    EMAIL_PORT = int(env("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "CBSRMS <no-reply@cbsrms.local>")

# --------------------------------------------------------------------------
# Messages -> Bootstrap alert classes
# --------------------------------------------------------------------------
MESSAGE_TAGS = {message_constants.ERROR: "danger"}

# --------------------------------------------------------------------------
# CBSRMS business rules (tune these without touching code)
# --------------------------------------------------------------------------
CBSRMS = {
    "COMPANY_NAME": env("COMPANY_NAME", "Your Logistics Co."),
    # A shipment with no update for this many hours is flagged as "stale".
    "STALE_UPDATE_HOURS": int(env("STALE_UPDATE_HOURS", "24")),
    # "Out for delivery" for this many hours with no confirmation -> alert.
    "POD_PENDING_HOURS": int(env("POD_PENDING_HOURS", "12")),
    # A shipment should have a current waybill within this many hours.
    "WAYBILL_GRACE_HOURS": int(env("WAYBILL_GRACE_HOURS", "24")),
    "MAX_UPLOAD_MB": int(env("MAX_UPLOAD_MB", "10")),
    "ALLOWED_UPLOAD_EXTENSIONS": ["pdf", "jpg", "jpeg", "png", "doc", "docx", "xls", "xlsx", "csv", "txt"],
    # How often (seconds) pages poll the server for live updates.
    "LIVE_POLL_SECONDS": int(env("LIVE_POLL_SECONDS", "8")),
}

# --------------------------------------------------------------------------
# Production security (active when DEBUG is off)
# --------------------------------------------------------------------------
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", "0"))
    SECURE_CONTENT_TYPE_NOSNIFF = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}
