"""KYCDocument rows must not orphan their Supabase Storage file on delete."""

from unittest.mock import patch

from django.test import TestCase

from apps.accounts.models import User, KYCDocument


class KYCDocumentStorageCleanupTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='kyc@example.com')

    @patch('apps.accounts.services.SupabaseStorage.delete_file')
    def test_deleting_document_deletes_its_storage_file(self, mock_delete):
        doc = KYCDocument.objects.create(
            user=self.user, document_type='aadhaar', document_url='RB-1/aadhaar_abc123.jpg',
        )

        doc.delete()

        mock_delete.assert_called_once_with('kyc-documents', 'RB-1/aadhaar_abc123.jpg')

    @patch('apps.accounts.services.SupabaseStorage.delete_file', side_effect=Exception('storage down'))
    def test_storage_failure_does_not_block_the_db_delete(self, mock_delete):
        doc = KYCDocument.objects.create(
            user=self.user, document_type='passport', document_url='RB-1/passport_z.jpg',
        )
        doc_pk = doc.pk

        doc.delete()  # must not raise

        self.assertFalse(KYCDocument.objects.filter(pk=doc_pk).exists())
