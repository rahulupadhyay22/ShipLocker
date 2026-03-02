from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse, HttpResponse
from django.views.decorators.cache import cache_page


def health_check(request):
    """Health check endpoint for load balancer / uptime monitoring."""
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
        "Disallow: /manage-rb-panel/",
        "Disallow: /admin/",
        "",
    ]
    return HttpResponse("\\n".join(lines), content_type="text/plain")


urlpatterns = [
    path('health/', health_check, name='health_check'),
    path('robots.txt', robots_txt, name='robots_txt'),
    path('manage-rb-panel/', admin.site.urls),  # Obscured admin URL (H2)
    path('accounts/', include('apps.accounts.urls')),
    path('locker/', include('apps.locker.urls')),
    path('shipments/', include('apps.shipments.urls')),
    path('kyc/', include('apps.kyc.urls')),
    path('payments/', include('apps.payments.urls')),
    path('', include('apps.content.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
