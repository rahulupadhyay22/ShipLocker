import json
import logging

from django.core.cache import cache
from django.utils.translation import get_language

logger = logging.getLogger(__name__)

# Active zones+rates barely change (admin-edited) but are read on every
# shipping-calculator page view and every create-shipment wizard load, from
# the same underlying data -- cache the computed snapshot instead of
# refetching identical rows per request. Invalidated immediately on
# ShippingZone/ShippingRate save/delete (see signals.py); TTL is a safety
# net in case a change happens outside the ORM (e.g. a raw SQL fixture load).
ZONES_CACHE_TTL = 60 * 60


def _zones_cache_key():
    # Keyed by locale so a future LocaleMiddleware/translated zone names
    # can't serve the wrong language from cache.
    return f'content:shipping_zones:{get_language()}'


def _compute_zones_data():
    from django.db.models import Prefetch
    from .models import ShippingZone, ShippingRate

    zones_data = []
    rates_qs = ShippingRate.objects.filter(is_active=True).order_by('service_type', 'min_weight')
    zones = ShippingZone.objects.filter(is_active=True).prefetch_related(Prefetch('rates', queryset=rates_qs))
    for zone in zones:
        zone_info = {
            'name': zone.name,
            'countries': zone.get_countries_list(),
            'rates': [
                {
                    'service_type': rate.service_type,
                    'min_weight': float(rate.min_weight),
                    'max_weight': float(rate.max_weight),
                    'rate_type': rate.rate_type,
                    'price': float(rate.price),
                    'delivery_min': rate.delivery_days_min,
                    'delivery_max': rate.delivery_days_max,
                }
                for rate in zone.rates.all()  # served from the prefetch above, no extra query
            ],
        }
        zones_data.append(zone_info)

    return zones_data


def get_zones_data():
    """Cached list of active zones + rates as plain dicts (safe to cache --
    no model instances). Regenerated on content change or TTL expiry."""
    key = _zones_cache_key()
    data = cache.get(key)
    if data is None:
        data = _compute_zones_data()
        cache.set(key, data, ZONES_CACHE_TTL)
        logger.info(f"Shipping zones cache miss, recomputed ({len(data)} zones)")
    return data


def build_zones_json():
    """Serialize active shipping zones + rates (incl. service_type) to JSON
    for client-side rate estimation. Shared by the shipping calculator page
    and the create-shipment wizard so both compute estimates identically."""
    return json.dumps(get_zones_data())


def invalidate_zones_cache(**kwargs):
    """Called on ShippingZone/ShippingRate save/delete -- see signals.py."""
    cache.delete(_zones_cache_key())


# ---------------------------------------------------------------------------
# ServiceCharge -- looked up by code on every TrunkAssist/consolidation-fee
# computation (apps/personal_shop/pricing.py, apps/payments/services.py).
# ---------------------------------------------------------------------------

SERVICE_CHARGE_CACHE_TTL = 60 * 60


def _service_charge_cache_key(code):
    return f'service_charge:{code}'


def get_service_charge(code):
    """Cached active ServiceCharge lookup by code. Some admin flows update
    ServiceCharge rows via .update(), which bypasses the post_save signal
    below entirely -- the TTL, not the signal, is the real safety net here."""
    key = _service_charge_cache_key(code)
    cached = cache.get(key)
    if cached is None:
        from .models import ServiceCharge
        charge = ServiceCharge.objects.filter(code=code, is_active=True).first()
        cache.set(key, charge or False, SERVICE_CHARGE_CACHE_TTL)
        return charge
    return cached or None


def invalidate_service_charge_cache(code, **kwargs):
    """Called on ServiceCharge save/delete -- see signals.py."""
    if code:
        cache.delete(_service_charge_cache_key(code))


# ---------------------------------------------------------------------------
# Active announcements -- read on every dashboard load.
# ---------------------------------------------------------------------------

ANNOUNCEMENTS_CACHE_KEY = 'active_announcements'
ANNOUNCEMENTS_CACHE_TTL = 15 * 60


def get_active_announcements():
    """Cached list of active announcements, newest first, capped at 5 (same
    shape the dashboard has always queried)."""
    data = cache.get(ANNOUNCEMENTS_CACHE_KEY)
    if data is None:
        from .models import Announcement
        data = list(Announcement.objects.filter(is_active=True).order_by('-created_at')[:5])
        cache.set(ANNOUNCEMENTS_CACHE_KEY, data, ANNOUNCEMENTS_CACHE_TTL)
    return data


def invalidate_announcements_cache(**kwargs):
    """Called on Announcement save/delete -- see signals.py."""
    cache.delete(ANNOUNCEMENTS_CACHE_KEY)
