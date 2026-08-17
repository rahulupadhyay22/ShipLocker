from django.apps import AppConfig


class LockerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.locker'
    verbose_name = 'Locker & Parcels'

    def ready(self):
        # Import signals to register them
        from . import signals  # noqa
