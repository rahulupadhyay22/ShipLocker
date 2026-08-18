"""
Django signals for automatic shipment tracking updates.

When a tracking number is added to a shipment, automatically fetch
the latest tracking status from the carrier API.
"""

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
import logging

from .models import Shipment, ShipmentDocument, TrackingEvent

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=ShipmentDocument)
def delete_document_from_storage(sender, instance, **kwargs):
    """Also fires when a Shipment is deleted and cascades to its documents.

    Skips the physical file if an Invoice still references the same path --
    Invoice.pdf_document_url is a durable financial record and must survive
    deletion of the ShipmentDocument row that happens to point at it too.
    """
    from apps.accounts.services import delete_storage_file
    from apps.payments.models import Invoice

    if Invoice.objects.filter(pdf_document_url=instance.document_url).exists():
        return

    delete_storage_file('invoices', instance.document_url)


# Track original values before save
_original_tracking_numbers = {}


@receiver(pre_save, sender=Shipment)
def store_original_tracking_number(sender, instance, **kwargs):
    """Store the original tracking number before save to detect changes."""
    if instance.pk:
        try:
            original = Shipment.objects.get(pk=instance.pk)
            _original_tracking_numbers[instance.pk] = original.tracking_number
        except Shipment.DoesNotExist:
            _original_tracking_numbers[instance.pk] = ''
    else:
        _original_tracking_numbers[instance.pk] = ''


@receiver(post_save, sender=Shipment)
def sync_tracking_on_number_change(sender, instance, created, **kwargs):
    """
    Automatically sync tracking when:
    1. A new tracking number is added
    2. The tracking number is changed
    """
    from .services.carrier_factory import CarrierFactory
    
    # Skip if no tracking number
    if not instance.tracking_number:
        return
    
    # Skip if carrier not supported
    if not CarrierFactory.is_supported(instance.carrier):
        logger.info(f"Carrier {instance.carrier} not supported for auto-sync")
        return
    
    # Check if tracking number changed
    original_tracking = _original_tracking_numbers.get(instance.pk, '')
    
    if instance.tracking_number == original_tracking:
        # No change in tracking number
        return
    
    # Clean up stored value
    if instance.pk in _original_tracking_numbers:
        del _original_tracking_numbers[instance.pk]
    
    logger.info(f"Auto-syncing tracking for shipment {instance.id} ({instance.tracking_number})")
    
    try:
        # Get tracking info from carrier
        result = CarrierFactory.track_shipment(
            instance.carrier,
            instance.tracking_number
        )
        
        if not result or not result.success:
            logger.warning(f"Could not track {instance.tracking_number}: {result.error if result else 'Unknown error'}")
            return
        
        # Update status if we got a valid one
        if result.status and result.status != instance.status:
            # Use update() to avoid triggering signal again
            Shipment.objects.filter(pk=instance.pk).update(
                status=result.status
            )
            logger.info(f"Updated shipment {instance.id} status to {result.status}")
            
            # Update timestamps
            if result.status == 'delivered':
                Shipment.objects.filter(pk=instance.pk, delivered_at__isnull=True).update(
                    delivered_at=result.timestamp or timezone.now()
                )
        
        # Save tracking events
        for event in result.events:
            # Check if we already have this event
            existing = TrackingEvent.objects.filter(
                shipment=instance,
                status=event.get('status', ''),
                event_timestamp=event.get('timestamp'),
            ).exists()
            
            if not existing:
                TrackingEvent.objects.create(
                    shipment=instance,
                    status=event.get('status', ''),
                    description=event.get('description', ''),
                    location=event.get('location', ''),
                    event_timestamp=event.get('timestamp'),
                    raw_data=event,
                )
        
        logger.info(f"Saved {len(result.events)} tracking events for shipment {instance.id}")
        
    except Exception as e:
        logger.error(f"Error auto-syncing tracking for {instance.id}: {e}")


# Track original payment_status before save (same shape as _original_tracking_numbers above)
_original_payment_status = {}


@receiver(pre_save, sender=Shipment)
def store_original_payment_status(sender, instance, **kwargs):
    """Store the original payment_status before save to detect a real transition into 'paid'."""
    if instance.pk:
        try:
            _original_payment_status[instance.pk] = Shipment.objects.get(pk=instance.pk).payment_status
        except Shipment.DoesNotExist:
            _original_payment_status[instance.pk] = ''
    else:
        _original_payment_status[instance.pk] = ''


@receiver(post_save, sender=Shipment)
def generate_invoice_on_paid(sender, instance, created, **kwargs):
    """Generate the GST invoice once a shipment's payment_status transitions
    into 'paid'. Deliberately thin — all the real work is InvoiceService's
    job. A failure here must never raise out of the payment-verification
    request; it's logged loudly and a manual admin action exists as backstop."""
    was = _original_payment_status.pop(instance.pk, '')

    if created or was == 'paid' or instance.payment_status != 'paid':
        return

    from apps.payments.services import InvoiceService

    try:
        InvoiceService.generate_for_shipment(instance, paid_at=instance.paid_at or timezone.now())
    except Exception:
        logger.exception(f"Invoice generation failed for shipment {instance.pk}")
