import requests
import logging

from indiabox.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)

# Fires synchronously from post_save signals on nearly every Parcel/Shipment
# write across the app, so a slow/down WhatsApp API must fail fast rather
# than tie up the saving request's thread.
_breaker = CircuitBreaker('whatsapp', fail_threshold=5, reset_timeout=60, max_concurrency=4)


def _mask_phone_number(number):
    """Redact all but the last 4 digits for logging -- phone numbers are
    PII and must not appear in plaintext in application logs."""
    digits = str(number)
    if len(digits) <= 4:
        return '*' * len(digits)
    return '*' * (len(digits) - 4) + digits[-4:]


class WhatsAppService:
    """
    Service to interact with WhatsApp Cloud API.
    Reads configuration from database (AppSettings model).
    """

    def __init__(self):
        self.api_version = 'v17.0'
        self._settings = None
    
    @property
    def settings(self):
        """Lazy load settings from database."""
        if self._settings is None:
            from apps.notifications.models import AppSettings
            self._settings = AppSettings.load()
        return self._settings
    
    @property
    def api_token(self):
        return self.settings.whatsapp_api_token if self.settings else None
    
    @property
    def phone_number_id(self):
        return self.settings.whatsapp_phone_number_id if self.settings else None
    
    @property
    def is_enabled(self):
        return self.settings.whatsapp_enabled if self.settings else False
    
    def get_template_name(self, event_type):
        """Get the template name for a specific event from settings."""
        if not self.settings:
            return event_type
        
        template_map = {
            'parcel_added': self.settings.template_parcel_added,
            'parcel_action_required': self.settings.template_parcel_action_required,
            'shipment_created': self.settings.template_shipment_created,
            'shipment_updated': self.settings.template_shipment_updated,
        }
        return template_map.get(event_type, event_type)

    def send_template_message(self, to_number, template_name, language_code='en_US', components=None):
        """
        Send a template message to a user.
        
        Args:
            to_number (str): Recipient's phone number in E.164 format (e.g., '919876543210')
            template_name (str): Name of the pre-approved template
            language_code (str): Language of the template
            components (list): List of variable components (parameters)
            
        Returns:
            dict: API response or None if failed
        """
        # Check if WhatsApp is enabled
        if not self.is_enabled:
            logger.info("WhatsApp notifications are disabled in settings")
            return None
            
        if not self.api_token or not self.phone_number_id:
            logger.warning("WhatsApp API credentials not configured")
            return None
        
        # Get the actual template name from settings
        actual_template = self.get_template_name(template_name)
            
        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }
        
        # Clean phone number (remove + if present)
        to_number = str(to_number).replace('+', '').strip()
        
        payload = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "template",
            "template": {
                "name": actual_template,
                "language": {
                    "code": language_code
                }
            }
        }
        
        if components:
            payload["template"]["components"] = components
            
        try:
            with _breaker.call():
                response = requests.post(url, headers=headers, json=payload, timeout=5)
                response.raise_for_status()
            logger.info(f"WhatsApp message sent to {_mask_phone_number(to_number)}")
            return response.json()
        except CircuitOpenError as e:
            logger.warning(f"WhatsApp send to {_mask_phone_number(to_number)} skipped: {e}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send WhatsApp message to {_mask_phone_number(to_number)}: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                # Status code only -- the response body can contain message
                # content or other account-identifying details from the
                # WhatsApp API and must not be logged in plaintext.
                logger.error(f"WhatsApp API error response status: {e.response.status_code}")
            return None

    def send_test_message(self, to_number):
        """
        Send the standard 'hello_world' template for testing.
        """
        return self.send_template_message(to_number, 'hello_world')
