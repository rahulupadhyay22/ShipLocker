from django.apps import AppConfig


class ContentConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.content'
    verbose_name = 'Content & Announcements'

    def ready(self):
        from . import signals  # noqa: F401
