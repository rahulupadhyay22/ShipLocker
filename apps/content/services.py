import json


def build_zones_json():
    """Serialize active shipping zones + rates (incl. service_type) to JSON
    for client-side rate estimation. Shared by the shipping calculator page
    and the create-shipment wizard so both compute estimates identically."""
    from .models import ShippingZone

    zones_data = []
    for zone in ShippingZone.objects.filter(is_active=True).prefetch_related('rates'):
        zone_info = {
            'name': zone.name,
            'countries': zone.get_countries_list(),
            'rates': [],
        }
        for rate in zone.rates.filter(is_active=True).order_by('service_type', 'min_weight'):
            zone_info['rates'].append({
                'service_type': rate.service_type,
                'min_weight': float(rate.min_weight),
                'max_weight': float(rate.max_weight),
                'rate_type': rate.rate_type,
                'price': float(rate.price),
                'delivery_min': rate.delivery_days_min,
                'delivery_max': rate.delivery_days_max,
            })
        zones_data.append(zone_info)

    return json.dumps(zones_data)
