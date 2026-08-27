"""
Django settings for CamelTrunk Global Locker project.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from urllib.parse import urlparse

# Load environment variables
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: don't run with debug turned on in production!
# Fails closed: a missing/unset DEBUG env var means DEBUG=False, not True.
DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY', '')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'django-insecure-dev-key'
    else:
        raise RuntimeError('SECRET_KEY environment variable must be set when DEBUG=False.')

# A real (non-sqlite-fallback) database means this could be holding real user
# data, so the dev encryption key fallback below must never apply to it —
# gated on the DB target rather than DEBUG, since DEBUG can be (mis)set True
# against a real database too.
_has_real_database = bool(
    os.getenv('DATABASE_URL', '').strip()
    or os.getenv('DATABASE_POOLER_URL', '').strip()
    or os.getenv('SUPABASE_POOLER_URL', '').strip()
)

# Key for encrypting sensitive AppSettings fields (e.g. supabase_service_role_key)
# at rest in the database — see indiabox/fields.py:EncryptedCharField.
FIELD_ENCRYPTION_KEY = os.getenv('FIELD_ENCRYPTION_KEY', '')
if not FIELD_ENCRYPTION_KEY:
    if not _has_real_database:
        # No real DB, so nothing encrypted under this key needs to survive a
        # restart — safe to generate an ephemeral one rather than hardcode a
        # secret in source.
        from cryptography.fernet import Fernet
        FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode()
    else:
        raise RuntimeError('FIELD_ENCRYPTION_KEY environment variable must be set when a real database is configured.')

allowed_hosts_env = [host.strip() for host in os.getenv('ALLOWED_HOSTS', '127.0.0.1,localhost').split(',') if host.strip()]

# Railway provides this automatically for public services.
railway_public_domain = os.getenv('RAILWAY_PUBLIC_DOMAIN', '').strip()
if railway_public_domain:
    allowed_hosts_env.append(railway_public_domain)

# Optional support if a full URL is provided via env.
railway_static_url = os.getenv('RAILWAY_STATIC_URL', '').strip()
if railway_static_url:
    parsed_domain = urlparse(railway_static_url).netloc.strip() or railway_static_url
    if parsed_domain:
        allowed_hosts_env.append(parsed_domain)

ALLOWED_HOSTS = list(dict.fromkeys(allowed_hosts_env))

# Base URL for building absolute links in outbound notifications (e.g. WhatsApp
# reminders) where no request object is available. Falls back to the Railway
# public domain, then the first allowed host.
SITE_URL = os.getenv('SITE_URL', '').strip() or (
    f'https://{railway_public_domain}' if railway_public_domain
    else f'https://{ALLOWED_HOSTS[0]}' if ALLOWED_HOSTS
    else 'http://localhost:8000'
)

# Note: Deprecated JAZZMIN settings removed. Django Unfold is used instead.

INSTALLED_APPS = [
    'unfold',  # Modern Admin UI
    'unfold.contrib.filters',
    'unfold.contrib.forms',
    'unfold.contrib.inlines',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Admin 2FA (see indiabox/admin_site.py — django.contrib.admin is gated
    # behind OTP verification for staff/superuser logins)
    'django_otp',
    'django_otp.plugins.otp_static',
    'django_otp.plugins.otp_totp',
    'two_factor',
    # Local apps
    'apps.accounts',
    'apps.locker',
    'apps.shipments',
    'apps.kyc',
    'apps.content',
    'apps.notifications',
    'apps.payments',
    'apps.personal_shop',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.gzip.GZipMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # Whitenoise for static files
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django_otp.middleware.OTPMiddleware',
    'indiabox.middleware.LockerCacheMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Custom Security Middleware
    'indiabox.middleware.RateLimitMiddleware',
    'indiabox.middleware.SecurityHeadersMiddleware',
    'indiabox.middleware.LoginAttemptMiddleware',
]

ROOT_URLCONF = 'indiabox.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'indiabox.context_processors.app_settings',
                'indiabox.context_processors.nav_counts',
            ],
        },
    },
]

WSGI_APPLICATION = 'indiabox.wsgi.application'

# Database - Supabase PostgreSQL
DATABASE_URL = os.getenv('DATABASE_URL', '').strip()
DATABASE_POOLER_URL = os.getenv('DATABASE_POOLER_URL', '').strip() or os.getenv('SUPABASE_POOLER_URL', '').strip()


def _normalize_supabase_pooler_url(raw_url: str) -> str:
    if not raw_url:
        return raw_url
    parsed = urlparse(raw_url)
    hostname = (parsed.hostname or '').lower()
    if hostname.endswith('pooler.supabase.com') and (parsed.port in (None, 5432)):
        auth = ''
        if parsed.username:
            auth = parsed.username
            if parsed.password:
                auth = f"{auth}:{parsed.password}"
            auth = f"{auth}@"
        corrected_netloc = f"{auth}{hostname}:6543"
        return parsed._replace(netloc=corrected_netloc).geturl()
    return raw_url


SELECTED_DATABASE_URL = _normalize_supabase_pooler_url(DATABASE_POOLER_URL) or DATABASE_URL

if SELECTED_DATABASE_URL:
    import dj_database_url
    DATABASES = {
        'default': dj_database_url.parse(SELECTED_DATABASE_URL, conn_max_age=600)
    }

    if DATABASES['default'].get('ENGINE') == 'django.db.backends.postgresql':
        db_options = DATABASES['default'].setdefault('OPTIONS', {})
        db_options.setdefault('connect_timeout', int(os.getenv('DB_CONNECT_TIMEOUT', '10')))

        # Supabase's connection pooler runs PgBouncer in transaction-pooling mode, which can
        # hand a query's later fetches to a different backend connection than the one that
        # opened it — Django's named server-side cursors (used for normal queryset iteration,
        # not just .iterator()) then fail with "cursor ... does not exist". Disabling them is
        # the standard fix for PgBouncer transaction pooling.
        DATABASES['default']['DISABLE_SERVER_SIDE_CURSORS'] = True

        # Optional: force IPv4 when provider network cannot reach IPv6 addresses.
        database_hostaddr = os.getenv('DATABASE_HOSTADDR', '').strip()
        if database_hostaddr:
            db_options['hostaddr'] = database_hostaddr

        # Ensure SSL is enabled for production database.
        if not DEBUG:
            db_options.setdefault('sslmode', 'require')
else:
    # Fallback to SQLite for development without Supabase
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# =============================================================================
# SECRET KEY VALIDATION
# =============================================================================
# Ensure SECRET_KEY is properly set in production
if not DEBUG:
    if SECRET_KEY == 'django-insecure-dev-key' or len(SECRET_KEY) < 50:
        import warnings
        warnings.warn(
            'SECRET_KEY is using default or weak value! '
            'Set a strong SECRET_KEY in environment variables for production.',
            RuntimeWarning
        )

# =============================================================================
# ERROR TRACKING (Sentry)
# =============================================================================
SENTRY_DSN = os.getenv('SENTRY_DSN', '').strip()
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration()],
        environment=os.getenv('SENTRY_ENVIRONMENT', 'production' if not DEBUG else 'development'),
        traces_sample_rate=float(os.getenv('SENTRY_TRACES_SAMPLE_RATE', '0.0')),
        send_default_pii=False,
    )

# Custom User Model
AUTH_USER_MODEL = 'accounts.User'

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Supabase Configuration
SUPABASE_URL = os.getenv('SUPABASE_URL', '')
SUPABASE_KEY = os.getenv('SUPABASE_KEY', '')
SUPABASE_ANON_KEY = os.getenv('SUPABASE_ANON_KEY', '')

# Locker Configuration
LOCKER_ADDRESS = os.getenv('LOCKER_ADDRESS', 'Unit 402, Warehouse Zone, Mumbai, India')

# WhatsApp Configuration
WHATSAPP_API_TOKEN = os.getenv('WHATSAPP_API_TOKEN', '')
WHATSAPP_PHONE_NUMBER_ID = os.getenv('WHATSAPP_PHONE_NUMBER_ID', '')
WHATSAPP_VERIFY_TOKEN = os.getenv('WHATSAPP_VERIFY_TOKEN', '')

# Login URLs
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/dashboard/'

# Admin OTP gate wires its own login redirect explicitly (indiabox/admin_site.py)
# rather than relying on two_factor's global-LOGIN_URL auto-patch — the site-wide
# LOGIN_URL above is the customer passwordless flow, not the staff 2FA one.
TWO_FACTOR_PATCH_ADMIN = False

# Rate limiting: per-category thresholds, all overridable via env.
# auth = login/OTP (per-IP AND per-account, exponential backoff on repeat abuse)
# public = unauthenticated read/calculator endpoints (fixed window, per-IP)
# authenticated = logged-in user actions (fixed window, per-IP, looser)
RATE_LIMIT_SETTINGS = {
    'auth': {
        'max_attempts': int(os.getenv('RATE_LIMIT_AUTH_MAX_ATTEMPTS', '5')),
        'window': int(os.getenv('RATE_LIMIT_AUTH_WINDOW', '300')),
        'backoff_base': int(os.getenv('RATE_LIMIT_AUTH_BACKOFF_BASE', '60')),
        'backoff_max': int(os.getenv('RATE_LIMIT_AUTH_BACKOFF_MAX', '3600')),
    },
    'public': {
        'max_attempts': int(os.getenv('RATE_LIMIT_PUBLIC_MAX_ATTEMPTS', '30')),
        'window': int(os.getenv('RATE_LIMIT_PUBLIC_WINDOW', '3600')),
    },
    'authenticated': {
        'max_attempts': int(os.getenv('RATE_LIMIT_AUTHENTICATED_MAX_ATTEMPTS', '50')),
        'window': int(os.getenv('RATE_LIMIT_AUTHENTICATED_WINDOW', '3600')),
    },
}
LOGOUT_REDIRECT_URL = '/'

# =============================================================================
# SECURITY SETTINGS
# =============================================================================

# =============================================================================
# CACHE CONFIGURATION (Redis when available, LocMemCache fallback)
# =============================================================================
REDIS_URL = os.getenv('REDIS_URL', '').strip()

if REDIS_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': REDIS_URL,
            'OPTIONS': {
                'CLIENT_CLASS': 'django_redis.client.DefaultClient',
                'SOCKET_CONNECT_TIMEOUT': 5,
                'SOCKET_TIMEOUT': 5,
                'RETRY_ON_TIMEOUT': True,
                'CONNECTION_POOL_KWARGS': {'max_connections': 20},
            },
            'KEY_PREFIX': 'indiabox',
            'TIMEOUT': 300,  # Default 5-minute TTL
        }
    }
    # Use Redis for sessions (much faster than DB-backed sessions)
    SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
    SESSION_CACHE_ALIAS = 'default'
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'indiabox-locmem',
        }
    }

# Session Security
SESSION_COOKIE_AGE = 86400  # 24 hours
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'  # Prevents CSRF via cross-site requests
CSRF_COOKIE_HTTPONLY = True  # Prevent JS from reading CSRF token

# Clickjacking Protection (allow same-origin admin modal/popup iframes)
X_FRAME_OPTIONS = 'SAMEORIGIN'

# Production Security (only when DEBUG is False)
if not DEBUG:
    # HTTPS/SSL Settings
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    
    # Cookie Security
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    
    # CSRF Trusted Origins (add your production domain)
    CSRF_TRUSTED_ORIGINS = [
        'https://' + host for host in ALLOWED_HOSTS if host not in ('localhost', '127.0.0.1')
    ]
    
    # HTTP Strict Transport Security
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    
    # XSS and Content Type Protection
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    
    # Referrer Policy
    SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'

# File Upload Settings
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # 5MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # 5MB
ALLOWED_UPLOAD_TYPES = [
    'application/pdf',
    'image/jpeg',
    'image/png',
    'image/jpg',
]
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5MB

# Logging Configuration – console (Railway/PaaS log stream) plus Sentry for
# the security logger specifically. Platform console logs are typically
# retained hours-to-days and aren't searchable; routing 'security' through
# Sentry gives it a durable, searchable destination without standing up a
# dedicated log drain. sentry_sdk.integrations.logging.EventHandler is a
# no-op when SENTRY_DSN isn't set (no Sentry client to send to), so this is
# safe to leave in place unconditionally.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'sentry': {
            # WARNING+ only -- INFO-level security logs (routine OTP/login
            # activity) still reach Sentry as breadcrumb context via its
            # default LoggingIntegration, without costing a standalone event.
            'level': 'WARNING',
            'class': 'sentry_sdk.integrations.logging.EventHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'security': {
            'handlers': ['console', 'sentry'],
            'level': 'INFO',
            'propagate': True,
        },
        'django.security': {
            'handlers': ['console', 'sentry'],
            'level': 'WARNING',
            'propagate': True,
        },
    },
}

# =============================================================================
# UNFOLD ADMIN CONFIGURATION - Modern Admin UI
# =============================================================================
from django.templatetags.static import static
from django.urls import reverse_lazy

UNFOLD = {
    "SITE_TITLE": "CamelTrunk Admin",
    "SITE_HEADER": "CamelTrunk",
    "SITE_SYMBOL": "package",  # Material icon
    "DASHBOARD_CALLBACK": "indiabox.dashboard.dashboard_callback",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "SHOW_SEARCH": False,  # Disable search modal
    "SHOW_SPOTLIGHT": False,  # Disable spotlight search to fix ESC issue
    "ENVIRONMENT": "indiabox.environment.environment_callback",
    "SCRIPTS": [
        lambda request: static("unfold/fix-search.js"),  # ESC key fix
    ],
    "COLORS": {
        "primary": {
            "50": "239 246 255",
            "100": "219 234 254",
            "200": "191 219 254",
            "300": "147 197 253",
            "400": "96 165 250",
            "500": "59 130 246",
            "600": "37 99 235",
            "700": "29 78 216",
            "800": "30 64 175",
            "900": "30 58 138",
            "950": "23 37 84",
        },
    },
    "SIDEBAR": {
        "show_search": False,  # No sidebar search
        "show_all_applications": False,  # Disable apps dropdown modal
        "navigation": [
            {
                "title": "Dashboard",
                "separator": True,
                "items": [
                    {
                        "title": "Dashboard",
                        "icon": "dashboard",
                        "link": reverse_lazy("admin:index"),
                    },
                ],
            },
            {
                "title": "Locker Management",
                "separator": True,
                "items": [
                    {
                        "title": "Parcels",
                        "icon": "inventory_2",
                        "link": reverse_lazy("admin:locker_parcel_changelist"),
                    },
                    {
                        "title": "Return Requests",
                        "icon": "keyboard_return",
                        "link": reverse_lazy("admin:locker_returnrequest_changelist"),
                    },
                    {
                        "title": "Discard Requests",
                        "icon": "delete",
                        "link": reverse_lazy("admin:locker_discardrequest_changelist"),
                    },
                    {
                        "title": "Batches",
                        "icon": "inventory",
                        "link": reverse_lazy("admin:locker_batch_changelist"),
                    },
                    {
                        "title": "User Quotas",
                        "icon": "confirmation_number",
                        "link": reverse_lazy("admin:locker_userquota_changelist"),
                    },
                ],
            },
            {
                "title": "Shipments",
                "separator": True,
                "items": [
                    {
                        "title": "All Shipments",
                        "icon": "local_shipping",
                        "link": reverse_lazy("admin:shipments_shipment_changelist"),
                    },
                    {
                        "title": "Declaration Approvals",
                        "icon": "assignment_turned_in",
                        "link": reverse_lazy("admin:shipments_declarationpendingshipment_changelist"),
                    },
                    {
                        "title": "Shipment Items",
                        "icon": "inventory",
                        "link": reverse_lazy("admin:shipments_shipmentitem_changelist"),
                    },
                    {
                        "title": "Shipment Documents",
                        "icon": "description",
                        "link": reverse_lazy("admin:shipments_shipmentdocument_changelist"),
                    },
                    {
                        "title": "Tracking Events",
                        "icon": "pin_drop",
                        "link": reverse_lazy("admin:shipments_trackingevent_changelist"),
                    },
                ],
            },
            {
                "title": "Payments",
                "separator": True,
                "items": [
                    {
                        "title": "Payments",
                        "icon": "payment",
                        "link": reverse_lazy("admin:payments_payment_changelist"),
                    },
                    {
                        "title": "Storage Fees",
                        "icon": "garage",
                        "link": reverse_lazy("admin:payments_batchcharge_changelist"),
                    },
                    {
                        "title": "Invoices",
                        "icon": "receipt",
                        "link": reverse_lazy("admin:payments_invoice_changelist"),
                    },
                ],
            },
            {
                "title": "TrunkAssist",
                "separator": True,
                "items": [
                    {
                        "title": "Requests",
                        "icon": "storefront",
                        "link": reverse_lazy("admin:personal_shop_personalshoprequest_changelist"),
                    },
                    {
                        "title": "Quotations",
                        "icon": "request_quote",
                        "link": reverse_lazy("admin:personal_shop_personalshopquotation_changelist"),
                    },
                ],
            },
            {
                "title": "Users & KYC",
                "separator": True,
                "items": [
                    {
                        "title": "Users",
                        "icon": "person",
                        "link": reverse_lazy("admin:accounts_user_changelist"),
                    },
                    {
                        "title": "Groups",
                        "icon": "groups",
                        "link": reverse_lazy("admin:auth_group_changelist"),
                    },
                    {
                        "title": "Lockers",
                        "icon": "lock",
                        "link": reverse_lazy("admin:accounts_locker_changelist"),
                    },
                    {
                        "title": "KYC Documents",
                        "icon": "verified_user",
                        "link": reverse_lazy("admin:accounts_kycdocument_changelist"),
                    },
                    {
                        "title": "Saved Addresses",
                        "icon": "home_pin",
                        "link": reverse_lazy("admin:accounts_savedaddress_changelist"),
                    },
                ],
            },
            {
                "title": "Content & Announcements",
                "separator": True,
                "items": [
                    {
                        "title": "Announcements",
                        "icon": "campaign",
                        "link": reverse_lazy("admin:content_announcement_changelist"),
                    },
                    {
                        "title": "Static Pages",
                        "icon": "article",
                        "link": reverse_lazy("admin:content_staticpage_changelist"),
                    },
                    {
                        "title": "Page Sections",
                        "icon": "layers",
                        "link": reverse_lazy("admin:content_pagesection_changelist"),
                    },
                    {
                        "title": "Service Charges",
                        "icon": "price_change",
                        "link": reverse_lazy("admin:content_servicecharge_changelist"),
                    },
                    {
                        "title": "Shipping Zones",
                        "icon": "public",
                        "link": reverse_lazy("admin:content_shippingzone_changelist"),
                    },
                    {
                        "title": "Shipping Rates",
                        "icon": "sell",
                        "link": reverse_lazy("admin:content_shippingrate_changelist"),
                    },
                    {
                        "title": "Admin Logs",
                        "icon": "receipt_long",
                        "link": reverse_lazy("admin:content_adminlog_changelist"),
                    },
                ],
            },
            {
                "title": "Settings",
                "separator": True,
                "items": [
                    {
                        "title": "App Settings",
                        "icon": "settings",
                        "link": reverse_lazy("admin:notifications_appsettings_changelist"),
                    },
                ],
            },
        ],
    },
}
