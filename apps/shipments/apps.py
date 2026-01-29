from django.apps import AppConfig


class ShipmentsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.shipments'
    verbose_name = 'Shipments'
    
    def ready(self):
        # Import signals to register them
        from . import signals  # noqa

