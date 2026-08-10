"""
WhatsApp Notification Signals

Automatically sends WhatsApp messages when key events occur.
"""
import logging
from django.core.cache import cache
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.conf import settings

logger = logging.getLogger(__name__)


def get_whatsapp_service():
    """Lazy import to avoid circular imports."""
    from apps.notifications.services import WhatsAppService
    return WhatsAppService()


def send_notification(user, template_name, components):
    """
    Helper function to send WhatsApp notification.
    Returns True if sent, False otherwise.
    """
    try:
        whatsapp_number = user.whatsapp_number
        
        if not whatsapp_number:
            logger.info(f"User {user.email} has no WhatsApp number, skipping notification")
            return False
            
        service = get_whatsapp_service()
        if not service.api_token:
            logger.warning("WhatsApp API not configured, skipping notification")
            return False
        
        result = service.send_template_message(
            to_number=whatsapp_number,
            template_name=template_name,
            components=components
        )
        
        if result:
            logger.info(f"WhatsApp '{template_name}' sent to {user.email}")
            return True
        else:
            logger.error(f"Failed to send WhatsApp '{template_name}' to {user.email}")
            return False
            
    except Exception as e:
        logger.error(f"Error sending notification: {str(e)}")
        return False


# ============================================================================
# PARCEL SIGNALS — uses Django cache instead of module-level dict (M9 fix)
# ============================================================================


@receiver(pre_save, sender='locker.Parcel')
def cache_parcel_status(sender, instance, **kwargs):
    """Cache the old status before save to detect changes."""
    if instance.pk:
        try:
            old_instance = sender.objects.get(pk=instance.pk)
            cache.set(f'parcel_old_status:{instance.pk}', old_instance.status, timeout=60)
        except sender.DoesNotExist:
            pass


@receiver(post_save, sender='locker.Parcel')
def notify_parcel_events(sender, instance, created, **kwargs):
    """
    Handle all parcel-related notifications:
    1. Parcel Added (new parcel created)
    2. Parcel Action Required (needs user review)
    """
    old_status = cache.get(f'parcel_old_status:{instance.pk}')
    cache.delete(f'parcel_old_status:{instance.pk}')
    user = instance.locker.user
    parcel_name = instance.item_name or "Your package"
    parcel_id = instance.display_id or str(instance.id)[:8]
    
    # EVENT 1: New Parcel Added
    if created:
        components = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": parcel_name},
                    {"type": "text", "text": parcel_id},
                ]
            }
        ]
        send_notification(user, "parcel_added", components)
        logger.info(f"📦 PARCEL ADDED: {parcel_id} for {user.email}")
    
    # EVENT 2: Parcel Action Required
    if instance.status == 'action_required':
        # Only notify if status just changed to action_required
        if created or (old_status and old_status != 'action_required'):
            components = [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": parcel_name},
                        {"type": "text", "text": parcel_id},
                    ]
                }
            ]
            send_notification(user, "parcel_action_required", components)
            logger.info(f"⚠️ ACTION REQUIRED: {parcel_id} for {user.email}")


# ============================================================================
# SHIPMENT SIGNALS — uses Django cache instead of module-level dict (M9 fix)
# ============================================================================


@receiver(pre_save, sender='shipments.Shipment')
def cache_shipment_status(sender, instance, **kwargs):
    """Cache the old status before save to detect changes."""
    if instance.pk:
        try:
            old_instance = sender.objects.get(pk=instance.pk)
            cache.set(f'shipment_old_status:{instance.pk}', old_instance.status, timeout=60)
        except sender.DoesNotExist:
            pass


@receiver(post_save, sender='shipments.Shipment')
def notify_shipment_events(sender, instance, created, **kwargs):
    """
    Handle all shipment-related notifications:
    1. Shipment Created (new shipment request)
    2. Shipment Updated (status changed)
    """
    old_status = cache.get(f'shipment_old_status:{instance.pk}')
    cache.delete(f'shipment_old_status:{instance.pk}')
    user = instance.user
    shipment_id = instance.display_id or str(instance.id)[:8]
    tracking = instance.tracking_number or "Pending"
    status_display = instance.get_status_display() if hasattr(instance, 'get_status_display') else instance.status
    
    # EVENT 3: Shipment Created
    if created:
        components = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": shipment_id},
                ]
            }
        ]
        send_notification(user, "shipment_created", components)
        logger.info(f"🚀 SHIPMENT CREATED: {shipment_id} for {user.email}")
    
    # EVENT 4: Shipment Status Updated
    elif old_status and old_status != instance.status:
        components = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": shipment_id},
                    {"type": "text", "text": status_display},
                    {"type": "text", "text": tracking},
                ]
            }
        ]
        send_notification(user, "shipment_updated", components)
        logger.info(f"📝 SHIPMENT UPDATED: {shipment_id} -> {status_display} for {user.email}")


# ============================================================================
# TRUNKASSIST (PERSONAL SHOP REQUEST) SIGNALS
# ============================================================================

PERSONAL_SHOP_STATUS_TEMPLATES = {
    'executive_assigned': 'personal_shop_executive_assigned',
    'quotation_ready': 'personal_shop_quotation_ready',
    'needs_info': 'personal_shop_needs_info',
    'purchased': 'personal_shop_purchased',
    'added_to_trunk': 'personal_shop_added_to_trunk',
}


@receiver(pre_save, sender='personal_shop.PersonalShopRequest')
def cache_personal_shop_request_status(sender, instance, **kwargs):
    """Cache the old status before save to detect changes."""
    if instance.pk:
        try:
            old_instance = sender.objects.get(pk=instance.pk)
            cache.set(f'pshop_old_status:{instance.pk}', old_instance.status, timeout=60)
        except sender.DoesNotExist:
            pass


@receiver(post_save, sender='personal_shop.PersonalShopRequest')
def notify_personal_shop_request_events(sender, instance, created, **kwargs):
    """Notify on TrunkAssist request status changes with a configured template."""
    old_status = cache.get(f'pshop_old_status:{instance.pk}')
    cache.delete(f'pshop_old_status:{instance.pk}')

    if created or old_status == instance.status:
        return

    template_name = PERSONAL_SHOP_STATUS_TEMPLATES.get(instance.status)
    if not template_name:
        return

    user = instance.locker.user
    request_id = instance.display_id or str(instance.id)[:8]
    status_display = instance.get_status_display()

    components = [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": request_id},
                {"type": "text", "text": status_display},
            ]
        }
    ]
    send_notification(user, template_name, components)
    logger.info(f"🛍️ TRUNKASSIST UPDATED: {request_id} -> {status_display} for {user.email}")
