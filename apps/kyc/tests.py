from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import ConsentRecord, KYCDocument, Locker, User

MINIMAL_PDF = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF'


class KYCUploadConsentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='kycuploader@example.com')
        Locker.objects.create(user=self.user)
        self.client.force_login(self.user)

    def _file(self):
        return SimpleUploadedFile('doc.pdf', MINIMAL_PDF, content_type='application/pdf')

    @patch('apps.kyc.views.upload_kyc_document', return_value='RB-1/aadhaar_x.pdf')
    def test_missing_consent_blocks_upload(self, mock_upload):
        response = self.client.post(reverse('kyc:upload'), {
            'document_type': 'aadhaar', 'document': self._file(),
        })

        self.assertEqual(response.status_code, 200)
        mock_upload.assert_not_called()
        self.assertFalse(KYCDocument.objects.filter(user=self.user).exists())

    @patch('apps.kyc.views.upload_kyc_document', return_value='RB-1/aadhaar_x.pdf')
    def test_consent_checked_uploads_and_logs_consent_record(self, mock_upload):
        response = self.client.post(reverse('kyc:upload'), {
            'document_type': 'aadhaar', 'document': self._file(), 'kyc_consent': 'on',
        })

        self.assertRedirects(response, reverse('kyc:list'))
        doc = KYCDocument.objects.get(user=self.user)
        self.assertEqual(doc.document_type, 'aadhaar')
        record = ConsentRecord.objects.get(user=self.user, consent_type='kyc_upload')
        self.assertTrue(record.policy_version)
