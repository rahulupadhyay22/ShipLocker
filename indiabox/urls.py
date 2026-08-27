from django.contrib import admin
from django.db import connection
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse, HttpResponse
from django.views.decorators.cache import cache_page
from two_factor.views import QRGeneratorView

import indiabox.admin_site  # noqa: F401 — applies the OTP-required admin.site patch
from indiabox.admin_site import (
    UnfoldBackupTokensView, UnfoldDisableView, UnfoldLoginView,
    UnfoldProfileView, UnfoldSetupCompleteView, UnfoldSetupView,
)

# two_factor's own views don't route through admin.site, so they miss the
# Unfold theme context (colors, branding) their templates rely on — see
# UnfoldContextMixin and templates/two_factor/_base.html.
tf_urls = (
    [
        path('account/login/', UnfoldLoginView.as_view(), name='login'),
        path('account/two_factor/setup/', UnfoldSetupView.as_view(), name='setup'),
        path('account/two_factor/qrcode/', QRGeneratorView.as_view(), name='qr'),
        path('account/two_factor/setup/complete/', UnfoldSetupCompleteView.as_view(), name='setup_complete'),
        path('account/two_factor/backup/tokens/', UnfoldBackupTokensView.as_view(), name='backup_tokens'),
        path('account/two_factor/', UnfoldProfileView.as_view(), name='profile'),
        path('account/two_factor/disable/', UnfoldDisableView.as_view(), name='disable'),
    ],
    'two_factor',
)


def health_check(request):
    """Health check endpoint for load balancer / uptime monitoring."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception as e:
        return JsonResponse({"status": "error", "database": str(e)}, status=503)
    return JsonResponse({"status": "ok"})


@cache_page(86400)  # Cache for 24 hours
def robots_txt(request):
    """Serve robots.txt from static files."""
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /accounts/",
        "Disallow: /locker/",
        "Disallow: /shipments/",
        "Disallow: /kyc/",
        "Disallow: /payments/",
        "Disallow: /personal-shop/",
        "Disallow: /manage-rb-panel/",
        "",
    ]
    return HttpResponse("\\n".join(lines), content_type="text/plain")


urlpatterns = [
    path('health/', health_check, name='health_check'),
    path('robots.txt', robots_txt, name='robots_txt'),
    path('manage-rb-panel/', include(tf_urls)),  # 2FA setup/login for admin staff
    path('manage-rb-panel/', admin.site.urls),  # Obscured admin URL (H2)
    path('accounts/', include('apps.accounts.urls')),
    path('locker/', include('apps.locker.urls')),
    path('shipments/', include('apps.shipments.urls')),
    path('kyc/', include('apps.kyc.urls')),
    path('payments/', include('apps.payments.urls')),
    path('personal-shop/', include('apps.personal_shop.urls')),
    path('', include('apps.content.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
