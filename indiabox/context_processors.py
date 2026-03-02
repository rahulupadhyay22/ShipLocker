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
    }
