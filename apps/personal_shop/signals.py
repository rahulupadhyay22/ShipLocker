from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import PersonalShopImage


@receiver(post_delete, sender=PersonalShopImage)
def delete_image_from_storage(sender, instance, **kwargs):
    """Also fires when a PersonalShopRequest is deleted and cascades to its images."""
    from apps.accounts.services import delete_storage_file
    delete_storage_file('parcel-images', instance.image_path)
