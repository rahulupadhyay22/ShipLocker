from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('apps.accounts.urls')),
    path('locker/', include('apps.locker.urls')),
    path('shipments/', include('apps.shipments.urls')),
    path('kyc/', include('apps.kyc.urls')),
    path('', include('apps.content.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
