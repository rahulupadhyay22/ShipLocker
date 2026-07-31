"""Context processors for app-wide template context."""

from apps.notifications.models import AppSettings


def app_settings(request):
    """Make AppSettings available in all templates as 'app_settings'."""
    try:
        settings = AppSettings.get_settings()
    except Exception:
        settings = None

    # Build a clean WhatsApp link number (strip +, spaces, dashes)
    whatsapp_number = ''
    if settings and settings.support_phone:
        whatsapp_number = settings.support_phone.replace('+', '').replace(' ', '').replace('-', '')

    return {
        'app_settings': settings,
        'whatsapp_link': whatsapp_number,
        'base_template': 'base.html' if request.user.is_authenticated else 'public_base.html',
    }


def nav_counts(request):
    """Incoming-parcel count for the sidebar/bottom-nav badge, shown on every authenticated page."""
    incoming_parcels_count = 0
    if request.user.is_authenticated:
        from apps.locker.models import Parcel
        incoming_parcels_count = Parcel.objects.filter(
            locker__user=request.user, status='action_required'
        ).count()

    return {'incoming_parcels_count': incoming_parcels_count}
