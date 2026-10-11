"""Django settings for the wallpaper API."""

from pathlib import Path
from urllib.parse import urlsplit

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CSRF_TRUSTED_ORIGINS=(list, []),
    RAILWAY_PUBLIC_DOMAIN=(str, ""),
    SECURE_SSL_REDIRECT=(bool, False),
    SECURE_HSTS_SECONDS=(int, 0),
    USE_LOCAL_MEDIA=(bool, True),
    R2_ACCOUNT_ID=(str, ""),
    R2_ACCESS_KEY_ID=(str, ""),
    R2_SECRET_ACCESS_KEY=(str, ""),
    R2_BUCKET_NAME=(str, ""),
    R2_ENDPOINT_URL=(str, ""),
    R2_PUBLIC_BASE_URL=(str, ""),
    R2_REGION=(str, "auto"),
    B2_ENDPOINT=(str, ""),
    B2_REGION=(str, ""),
    B2_KEY_ID=(str, ""),
    B2_APP_KEY=(str, ""),
    B2_BUCKET=(str, ""),
    B2_CDN_DOMAIN=(str, ""),
    B2_SIGNED_URLS=(bool, False),
    B2_URL_EXPIRE_SECONDS=(int, 3600),
    R2_SIGNED_URLS=(bool, False),
    R2_URL_EXPIRE_SECONDS=(int, 21600),
    REDIS_URL=(str, ""),
    API_CACHE_SECONDS=(int, 300),
    API_CLIENT_MAX_AGE=(int, 300),
    DB_CONN_MAX_AGE=(int, 60),
    SCORES_RECALC_MINUTES=(int, 10),
    SCORES_RECALC_ASYNC=(bool, True),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="dev-only-insecure-key-change-me")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

# --- Railway -------------------------------------------------------------
# Railway injects RAILWAY_PUBLIC_DOMAIN at runtime, so the deployed host does
# not have to be known (or redeployed) when the domain changes.
RAILWAY_PUBLIC_DOMAIN = env("RAILWAY_PUBLIC_DOMAIN")
if RAILWAY_PUBLIC_DOMAIN:
    ALLOWED_HOSTS = [
        *ALLOWED_HOSTS,
        RAILWAY_PUBLIC_DOMAIN,
        # Railway's deploy health check calls the app with this Host header;
        # without it the check gets a 400 and the deploy is rolled back.
        "healthcheck.railway.app",
    ]
    CSRF_TRUSTED_ORIGINS = [*CSRF_TRUSTED_ORIGINS, f"https://{RAILWAY_PUBLIC_DOMAIN}"]

# Railway terminates TLS at its edge and forwards plain HTTP inward. Without
# this Django reads every request as insecure: admin login over https fails the
# CSRF origin check, and build_absolute_uri() would put http:// into file_path.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

if not DEBUG:
    # The admin session is the only thing worth stealing here, and in
    # production it is always reached over https.
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Both of the following stay opt-in. A blanket redirect can make Railway's
    # health check follow a 301 and fail the deploy, and HSTS is effectively
    # irreversible for the length of its max-age - turn them on deliberately,
    # once a custom domain is settled and serving https only.
    SECURE_SSL_REDIRECT = env("SECURE_SSL_REDIRECT")
    SECURE_REDIRECT_EXEMPT = [r"^api/v1/wallpapers/categories/?$"]
    SECURE_HSTS_SECONDS = env("SECURE_HSTS_SECONDS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Trigram search lookups; inert on SQLite.
    "django.contrib.postgres",
    "rest_framework",
    "storages",
    "wallpapers",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Railway runs no separate web server, so WhiteNoise serves the admin's
    # static files straight from the app process.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

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

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        # A project checkout should work without a separately installed database.
        # Deployments can still supply a PostgreSQL URL through DATABASE_URL.
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
}
# Reuse connections between requests instead of reconnecting every time; the
# health check drops one that the database closed underneath us.
DATABASES["default"]["CONN_MAX_AGE"] = env("DB_CONN_MAX_AGE")
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

# --- Caching ---------------------------------------------------------------
# API responses are cached (see wallpapers/cache.py). With REDIS_URL every
# worker and replica shares one cache; without it each process keeps its own,
# which is still correct because invalidation goes through the database.
REDIS_URL = env("REDIS_URL")
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "TIMEOUT": 300,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "OPTIONS": {"MAX_ENTRIES": 5000},
        }
    }
API_CACHE_SECONDS = env("API_CACHE_SECONDS")
API_CLIENT_MAX_AGE = env("API_CLIENT_MAX_AGE")

# Trending is rebuilt from the last 7 days at most this often, started by
# incoming events (in a background thread unless SCORES_RECALC_ASYNC=False).
SCORES_RECALC_MINUTES = env("SCORES_RECALC_MINUTES")
SCORES_RECALC_ASYNC = env("SCORES_RECALC_ASYNC")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"

# The API contract serves naive UTC timestamps, so the whole stack stores UTC.
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Media storage -------------------------------------------------------
# USE_LOCAL_MEDIA lets the API run without object-storage credentials. In that mode files
# land in ./media and the serializer absolutizes file_path against the request;
# with object storage the backend already hands us an absolute public url.

USE_LOCAL_MEDIA = env("USE_LOCAL_MEDIA")

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

if USE_LOCAL_MEDIA:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
    }
else:
    _r2_values = {
        "account_id": env("R2_ACCOUNT_ID"),
        "access_key_id": env("R2_ACCESS_KEY_ID"),
        "secret_access_key": env("R2_SECRET_ACCESS_KEY"),
        "bucket_name": env("R2_BUCKET_NAME"),
        "endpoint_url": env("R2_ENDPOINT_URL"),
        "public_base_url": env("R2_PUBLIC_BASE_URL"),
    }
    _use_r2 = any(_r2_values.values())

    if _use_r2:
        _storage_values = {
            "endpoint_url": _r2_values["endpoint_url"]
            or (
                f"https://{_r2_values['account_id']}.r2.cloudflarestorage.com"
                if _r2_values["account_id"]
                else ""
            ),
            "region": env("R2_REGION"),
            "access_key_id": _r2_values["access_key_id"],
            "secret_access_key": _r2_values["secret_access_key"],
            "bucket_name": _r2_values["bucket_name"],
            "public_base_url": _r2_values["public_base_url"],
        }
        _storage_label = "R2"
    else:
        _storage_values = {
            "endpoint_url": env("B2_ENDPOINT"),
            "region": env("B2_REGION"),
            "access_key_id": env("B2_KEY_ID"),
            "secret_access_key": env("B2_APP_KEY"),
            "bucket_name": env("B2_BUCKET"),
            "public_base_url": env("B2_CDN_DOMAIN"),
        }
        _storage_label = "B2"

    _required_storage_values = [
        "endpoint_url",
        "access_key_id",
        "secret_access_key",
        "bucket_name",
        "public_base_url" if _use_r2 else "region",
    ]
    _missing_storage_values = [
        name for name in _required_storage_values if not _storage_values[name]
    ]
    if _missing_storage_values:
        raise ImproperlyConfigured(
            f"{_storage_label} storage requires configured values for: "
            + ", ".join(_missing_storage_values)
        )

    AWS_S3_ENDPOINT_URL = _storage_values["endpoint_url"]
    AWS_S3_REGION_NAME = _storage_values["region"] or "auto"
    AWS_ACCESS_KEY_ID = _storage_values["access_key_id"]
    AWS_SECRET_ACCESS_KEY = _storage_values["secret_access_key"]
    AWS_STORAGE_BUCKET_NAME = _storage_values["bucket_name"]
    AWS_S3_ADDRESSING_STYLE = "virtual"
    # Both upload paths include a UUID, so a name collision is effectively
    # impossible. Avoiding django-storages' existence (HeadObject) check also
    # means a write-only object-storage key can upload media.
    AWS_S3_FILE_OVERWRITE = True
    AWS_DEFAULT_ACL = None
    # Every stored name contains a fresh UUID and is never rewritten, so CDNs
    # and phones may keep a file forever.
    AWS_S3_OBJECT_PARAMETERS = {"CacheControl": "public, max-age=31536000, immutable"}

    if _use_r2:
        _public_url = urlsplit(_storage_values["public_base_url"])
        if _public_url.scheme != "https" or not _public_url.netloc:
            raise ImproperlyConfigured(
                "R2_PUBLIC_BASE_URL must be an absolute HTTPS URL for the public bucket."
            )
        if env("R2_SIGNED_URLS"):
            # Private bucket: every url is presigned and expires, so a copied
            # link stops working. Signed urls cannot use the public domain.
            AWS_QUERYSTRING_AUTH = True
            AWS_QUERYSTRING_EXPIRE = env("R2_URL_EXPIRE_SECONDS")
        else:
            AWS_S3_CUSTOM_DOMAIN = _public_url.netloc + _public_url.path.rstrip("/")
            AWS_S3_URL_PROTOCOL = "https:"
            AWS_QUERYSTRING_AUTH = False
    else:
        # B2 may use unsigned public URLs or signed URLs for private buckets.
        B2_SIGNED_URLS = env("B2_SIGNED_URLS")
        AWS_QUERYSTRING_AUTH = B2_SIGNED_URLS
        AWS_QUERYSTRING_EXPIRE = env("B2_URL_EXPIRE_SECONDS")

        _cdn_domain = _storage_values["public_base_url"]
        if _cdn_domain:
            if B2_SIGNED_URLS:
                # storages builds the url from the custom domain and returns it
                # unsigned (s3.py url()), so a CDN in front of a private bucket
                # would serve 401s. Fail loudly rather than at request time.
                raise ImproperlyConfigured(
                    "B2_CDN_DOMAIN cannot be combined with B2_SIGNED_URLS: urls "
                    "built from a custom domain are never signed. Use a public "
                    "bucket with the CDN, or drop the CDN."
                )
            AWS_S3_CUSTOM_DOMAIN = _cdn_domain

    STORAGES = {
        "default": {"BACKEND": "storages.backends.s3.S3Storage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
    }

# --- DRF -----------------------------------------------------------------
# Public reads, JSON only, with our own envelope; the stock pagination classes
# do not apply.

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "EXCEPTION_HANDLER": "wallpapers.exceptions.api_exception_handler",
    "UNAUTHENTICATED_USER": None,
}
