"""
Django settings for IndiaBox Global Locker project.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from urllib.parse import urlparse

# Load environment variables
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-dev-key')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DEBUG', 'True').lower() == 'true'

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

# =========================================
# JAZZMIN ADMIN UI CONFIGURATION
# =========================================
JAZZMIN_SETTINGS = {
    # Title & Branding
    "site_title": "IndiaBox Admin",
    "site_header": "IndiaBox",
    "site_brand": "IndiaBox",
    "site_logo": None,
    "login_logo": None,
    "welcome_sign": "Welcome to IndiaBox Admin",
    "copyright": "IndiaBox Global Locker",
    
    # User Menu
    "user_avatar": None,
    
    # Top Menu
    "topmenu_links": [
        {"name": "Home", "url": "admin:index", "permissions": ["auth.view_user"]},
        {"name": "View Site", "url": "/", "new_window": True},
        {"app": "accounts"},
    ],
    
    # Sidebar
    "show_sidebar": True,
    "navigation_expanded": True,
    "hide_apps": [],
    "hide_models": ["notifications.notificationsettings"],
    
    # App & Model Ordering + Custom Names
    "order_with_respect_to": [
        "accounts",
        "locker",
        "shipments",
        "content",
        "notifications",
        "payments",
        "auth",
        "admin",
    ],
    
    # Custom App Names in Sidebar
    "custom_links": {},
    
    # Icons (Font Awesome 5)
    "icons": {
        "auth": "fas fa-users-cog",
        "auth.user": "fas fa-user",
        "auth.Group": "fas fa-users",
        "accounts": "fas fa-user-shield",
        "accounts.user": "fas fa-user-circle",
        "accounts.locker": "fas fa-inbox",
        "accounts.kycdocument": "fas fa-id-card",
        "locker": "fas fa-warehouse",
        "locker.parcel": "fas fa-box",
        "locker.parcelimage": "fas fa-images",
        "locker.returnrequest": "fas fa-undo-alt",
        "locker.discardrequest": "fas fa-trash-alt",
        "shipments": "fas fa-shipping-fast",
        "shipments.shipment": "fas fa-truck",
        "shipments.shipmentitem": "fas fa-cubes",
        "shipments.shipmentdocument": "fas fa-file-alt",
        "shipments.trackingevent": "fas fa-map-marker-alt",
        "shipments.declarationpendingshipment": "fas fa-clipboard-check",
        "content": "fas fa-globe",
        "content.staticpage": "fas fa-file-alt",
        "content.pagesection": "fas fa-puzzle-piece",
        "content.servicecharge": "fas fa-rupee-sign",
        "content.announcement": "fas fa-bullhorn",
        "content.shippingzone": "fas fa-map-marked-alt",
        "content.shippingrate": "fas fa-tags",
        "content.adminlog": "fas fa-clipboard-list",
        "notifications": "fas fa-sliders-h",
        "notifications.appsettings": "fas fa-cogs",
        "payments": "fas fa-credit-card",
        "payments.payment": "fas fa-money-check-alt",
        "payments.storagefee": "fas fa-warehouse",
        "admin": "fas fa-history",
        "admin.logentry": "fas fa-clipboard-list",
    },
    "default_icon_parents": "fas fa-folder",
    "default_icon_children": "fas fa-circle",
    
    # UI Tweaks
    "related_modal_active": True,
    "custom_css": "css/admin_custom.css",
    "custom_js": None,
    "use_google_fonts_cdn": True,
    "show_ui_builder": False,
    
    # Change View
    "changeform_format": "horizontal_tabs",
    "changeform_format_overrides": {
        "auth.user": "collapsible",
        "auth.group": "vertical_tabs",
        "notifications.appsettings": "vertical_tabs",
    },
}

JAZZMIN_UI_TWEAKS = {
    "navbar_small_text": False,
    "footer_small_text": True,
    "body_small_text": False,
    "brand_small_text": False,
    "brand_colour": "navbar-dark",
    "accent": "accent-primary",
    "navbar": "navbar-dark navbar-primary",
    "no_navbar_border": True,
    "navbar_fixed": True,
    "layout_boxed": False,
    "footer_fixed": False,
    "sidebar_fixed": True,
    "sidebar": "sidebar-dark-primary",
    "sidebar_nav_small_text": False,
    "sidebar_disable_expand": False,
    "sidebar_nav_child_indent": True,
    "sidebar_nav_compact_style": False,
    "sidebar_nav_legacy_style": False,
    "sidebar_nav_flat_style": False,
    "theme": "darkly",
    "dark_mode_theme": "darkly",
    "button_classes": {
        "primary": "btn-primary",
        "secondary": "btn-secondary",
        "info": "btn-info",
        "warning": "btn-warning",
        "danger": "btn-danger",
        "success": "btn-success"
    },
    "actions_sticky_top": True,
}

# Application definition
INSTALLED_APPS = [
    'jazzmin',  # Modern Admin UI
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Local apps
    'apps.accounts',
    'apps.locker',
    'apps.shipments',
    'apps.kyc',
    'apps.content',
    'apps.notifications',
    'apps.payments',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # Whitenoise for static files
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
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
LOGOUT_REDIRECT_URL = '/'

# =============================================================================
# SECURITY SETTINGS
# =============================================================================

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

# Logging Configuration – console only (safe for Railway / any PaaS)
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
    },
    'loggers': {
        'security': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
        'django.security': {
            'handlers': ['console'],
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
    "SITE_TITLE": "IndiaBox Admin",
    "SITE_HEADER": "IndiaBox",
    "SITE_SYMBOL": "package",  # Material icon
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
                        "title": "Lockers",
                        "icon": "lock",
                        "link": reverse_lazy("admin:accounts_locker_changelist"),
                    },
                    {
                        "title": "KYC Documents",
                        "icon": "verified_user",
                        "link": reverse_lazy("admin:accounts_kycdocument_changelist"),
                    },
                ],
            },
            {
                "title": "Content",
                "separator": True,
                "items": [
                    {
                        "title": "Static Pages",
                        "icon": "article",
                        "link": reverse_lazy("admin:content_staticpage_changelist"),
                    },
                    {
                        "title": "Service Charges",
                        "icon": "payments",
                        "link": reverse_lazy("admin:content_servicecharge_changelist"),
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
