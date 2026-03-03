"""Razorpay payment verification and order creation service."""

import hmac
import hashlib
import logging
from decimal import Decimal

logger = logging.getLogger('security')


class RazorpayService:
    """Service for Razorpay payment operations with signature verification."""

    def __init__(self):
        self._settings = None

    @property
    def settings(self):
        if self._settings is None:
            from apps.notifications.models import AppSettings
            self._settings = AppSettings.load()
        return self._settings

    @property
    def key_id(self):
        return self.settings.razorpay_key_id if self.settings else ''

    @property
    def key_secret(self):
        return self.settings.razorpay_key_secret if self.settings else ''

    @property
    def is_enabled(self):
        return (
            self.settings.razorpay_enabled if self.settings else False
        ) and self.key_id and self.key_secret

    def verify_payment_signature(self, order_id: str, payment_id: str, signature: str) -> bool:
        """
        Verify Razorpay payment signature to prevent spoofing.

        The signature is an HMAC-SHA256 hash of 'order_id|payment_id'
        using the Razorpay key_secret.

        Args:
            order_id: Razorpay order ID (order_xxxxx)
            payment_id: Razorpay payment ID (pay_xxxxx)
            signature: Razorpay signature from checkout response

        Returns:
            True if signature is valid, False otherwise
        """
        if not self.key_secret:
            logger.error("Razorpay key_secret not configured — cannot verify signature")
            return False

        message = f"{order_id}|{payment_id}"
        expected_signature = hmac.new(
            self.key_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        is_valid = hmac.compare_digest(expected_signature, signature)

        if not is_valid:
            logger.warning(
                f"Razorpay signature mismatch: order={order_id}, payment={payment_id}"
            )
        else:
            logger.info(
                f"Razorpay payment verified: order={order_id}, payment={payment_id}"
            )

        return is_valid

    def create_order(self, amount_paise: int, currency: str = 'INR', receipt: str = '', notes: dict = None):
        """
        Create a Razorpay order via API.

        Args:
            amount_paise: Amount in paise (e.g., 50000 = ₹500)
            currency: Currency code (default INR)
            receipt: Internal receipt/reference ID
            notes: Optional metadata dict

        Returns:
            Razorpay order dict or None on error
        """
        import requests

        if not self.is_enabled:
            logger.warning("Razorpay not enabled or credentials missing")
            return None

        url = 'https://api.razorpay.com/v1/orders'
        payload = {
            'amount': amount_paise,
            'currency': currency,
            'receipt': receipt,
        }
        if notes:
            payload['notes'] = notes

        try:
            response = requests.post(
                url,
                json=payload,
                auth=(self.key_id, self.key_secret),
                timeout=30,
            )
            response.raise_for_status()
            order = response.json()
            logger.info(f"Razorpay order created: {order.get('id')}")
            return order
        except requests.exceptions.RequestException as e:
            logger.error(f"Razorpay order creation failed: {e}")
            return None

    def verify_webhook_signature(self, body: bytes, signature: str, webhook_secret: str) -> bool:
        """
        Verify Razorpay webhook signature.

        Args:
            body: Raw request body bytes
            signature: X-Razorpay-Signature header value
            webhook_secret: Your webhook secret from Razorpay dashboard

        Returns:
            True if valid
        """
        expected = hmac.new(
            webhook_secret.encode('utf-8'),
            body,
            hashlib.sha256
        ).hexdigest()

        is_valid = hmac.compare_digest(expected, signature)
        if not is_valid:
            logger.warning("Razorpay webhook signature mismatch")
        return is_valid


def _get_daily_storage_fee_amount() -> Decimal:
    """Resolve daily storage fee amount from active service charges.

    Looks for an active ServiceCharge containing both "storage" and "day" in the name,
    then falls back to any active storage charge. If not configured, uses ₹50/day.
    """
    from django.db.models import Q
    from apps.content.models import ServiceCharge

    named_daily_charge = ServiceCharge.objects.filter(
        Q(name__icontains='storage') & Q(name__icontains='day'),
        is_active=True,
    ).order_by('updated_at').first()
    if named_daily_charge:
        return Decimal(str(named_daily_charge.amount))

    generic_storage_charge = ServiceCharge.objects.filter(
        is_active=True,
        name__icontains='storage',
    ).order_by('updated_at').first()
    if generic_storage_charge:
        return Decimal(str(generic_storage_charge.amount))

    return Decimal('50.00')


def ensure_storage_fee_for_parcel(parcel):
    """Create or update pending StorageFee automatically after 30 free days."""
    from .models import StorageFee

    if not parcel or not parcel.received_at:
        return None

    overdue_days = max(0, parcel.storage_days - 30)
    if overdue_days <= 0:
        return None

    daily_fee = _get_daily_storage_fee_amount()
    total_fee = daily_fee * Decimal(overdue_days)

    storage_fee = StorageFee.objects.filter(
        parcel=parcel,
        status='pending',
    ).order_by('-created_at').first()

    if storage_fee:
        updated = False
        if storage_fee.days_overdue != overdue_days:
            storage_fee.days_overdue = overdue_days
            updated = True
        if storage_fee.fee_amount != total_fee:
            storage_fee.fee_amount = total_fee
            updated = True
        if updated:
            storage_fee.save(update_fields=['days_overdue', 'fee_amount'])
        return storage_fee

    # Do not auto-create another fee if one is already paid/waived.
    historical_fee = StorageFee.objects.filter(parcel=parcel).exists()
    if historical_fee:
        return None

    return StorageFee.objects.create(
        parcel=parcel,
        fee_amount=total_fee,
        days_overdue=overdue_days,
        status='pending',
    )
