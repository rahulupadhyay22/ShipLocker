import logging

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import PersonalShopImage, PersonalShopRequest

logger = logging.getLogger('security')


@receiver(post_delete, sender=PersonalShopImage)
def delete_image_from_storage(sender, instance, **kwargs):
    """Also fires when a PersonalShopRequest is deleted and cascades to its images."""
    from apps.accounts.services import delete_storage_file
    delete_storage_file('parcel-images', instance.image_path)


# Track original status before save (same shape as apps/shipments/signals.py's
# _original_payment_status, so a real transition into 'paid' can be detected).
_original_status = {}


@receiver(pre_save, sender=PersonalShopRequest)
def store_original_status(sender, instance, **kwargs):
    if instance.pk:
        try:
            _original_status[instance.pk] = PersonalShopRequest.objects.get(pk=instance.pk).status
        except PersonalShopRequest.DoesNotExist:
            _original_status[instance.pk] = ''
    else:
        _original_status[instance.pk] = ''


@receiver(post_save, sender=PersonalShopRequest)
def generate_invoice_on_paid(sender, instance, created, **kwargs):
    """Generate the TrunkAssist invoice once a request's status transitions
    into 'paid'. Deliberately thin, mirrors apps/shipments/signals.py's
    generate_invoice_on_paid — a failure here must never raise out of the
    payment-verification request; it's logged loudly instead."""
    was = _original_status.pop(instance.pk, '')

    if created or was == 'paid' or instance.status != 'paid':
        return

    from apps.payments.services import PersonalShopInvoiceService

    try:
        PersonalShopInvoiceService.generate_for_request(instance)
    except Exception:
        logger.exception(f"TrunkAssist invoice generation failed for request {instance.pk}")
