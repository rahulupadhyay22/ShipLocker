from django.core.management.base import BaseCommand
from apps.notifications.services import WhatsAppService

class Command(BaseCommand):
    help = 'Send a test WhatsApp message to a number'

    def add_arguments(self, parser):
        parser.add_argument('phone_number', type=str, help='Target phone number (e.g. 919876543210)')

    def handle(self, *args, **kwargs):
        phone_number = kwargs['phone_number']
        self.stdout.write(f"Sending test message to {phone_number}...")
        
        service = WhatsAppService()
        if not service.api_token:
            self.stdout.write(self.style.ERROR("Error: WHATSAPP_API_TOKEN is not set in settings/env."))
            return

        # Uses 'hello_world' template which is pre-approved for all Sandbox/Test accounts
        result = service.send_test_message(phone_number)
        
        if result:
            self.stdout.write(self.style.SUCCESS(f"Message sent successfully! Response: {result}"))
        else:
            self.stdout.write(self.style.ERROR("Failed to send message. Check logs for details."))
