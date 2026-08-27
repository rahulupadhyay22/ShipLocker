from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .models import Announcement, ServiceCharge, ShippingZone, ShippingRate
from .services import (
    invalidate_announcements_cache,
    invalidate_service_charge_cache,
    invalidate_zones_cache,
)


@receiver(post_save, sender=ShippingZone)
@receiver(post_delete, sender=ShippingZone)
@receiver(post_save, sender=ShippingRate)
@receiver(post_delete, sender=ShippingRate)
def clear_zones_cache(sender, **kwargs):
    invalidate_zones_cache()


@receiver(post_save, sender=ServiceCharge)
@receiver(post_delete, sender=ServiceCharge)
def clear_service_charge_cache(sender, instance, **kwargs):
    invalidate_service_charge_cache(instance.code)


@receiver(post_save, sender=Announcement)
@receiver(post_delete, sender=Announcement)
def clear_announcements_cache(sender, **kwargs):
    invalidate_announcements_cache()
