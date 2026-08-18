from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import KYCDocument


@receiver(post_delete, sender=KYCDocument)
def delete_kyc_document_from_storage(sender, instance, **kwargs):
    from .services import delete_storage_file
    delete_storage_file('kyc-documents', instance.document_url)
