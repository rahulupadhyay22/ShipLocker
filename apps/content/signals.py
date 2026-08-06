from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .models import ShippingZone, ShippingRate
from .services import invalidate_zones_cache


@receiver(post_save, sender=ShippingZone)
@receiver(post_delete, sender=ShippingZone)
@receiver(post_save, sender=ShippingRate)
@receiver(post_delete, sender=ShippingRate)
def clear_zones_cache(sender, **kwargs):
    invalidate_zones_cache()
