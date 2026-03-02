"""Razorpay payment verification and order creation service."""

import hmac
import hashlib
import logging

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
