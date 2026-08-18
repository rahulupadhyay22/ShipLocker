"""upload_invoice() must route into my-trunk/ vs personal-shopper/ based on
whether the parcel originated from a TrunkAssist purchase, and must use a
fixed filename per parcel (so re-uploads overwrite instead of orphaning)."""

from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.locker.utils import upload_invoice


class UploadInvoicePathTests(TestCase):
    def _file(self, name='receipt.pdf'):
        return SimpleUploadedFile(name, b'%PDF-1.4 fake content', content_type='application/pdf')

    @patch('apps.accounts.services.SupabaseStorage.upload_file')
    def test_regular_parcel_goes_under_my_trunk(self, mock_upload):
        path = upload_invoice(self._file(), locker_id='RB-12345', parcel_display_id='RB-12345-P001')

        self.assertEqual(path, 'my-trunk/RB-12345/RB-12345-P001/invoice.pdf')
        mock_upload.assert_called_once()
        self.assertEqual(mock_upload.call_args.kwargs['bucket_name'], 'invoices')
        self.assertEqual(mock_upload.call_args.kwargs['file_path'], 'my-trunk/RB-12345/RB-12345-P001/invoice.pdf')
        # Fixed path is only actually overwritable if upload passes upsert=True --
        # Supabase Storage rejects a write to an existing path otherwise.
        self.assertTrue(mock_upload.call_args.kwargs['upsert'])

    @patch('apps.accounts.services.SupabaseStorage.upload_file')
    def test_personal_shop_parcel_goes_under_personal_shopper(self, mock_upload):
        path = upload_invoice(
            self._file(), locker_id='RB-12345', parcel_display_id='RB-12345-P002', is_personal_shop=True,
        )

        self.assertEqual(path, 'personal-shopper/RB-12345/RB-12345-P002/invoice.pdf')

    @patch('apps.accounts.services.SupabaseStorage.upload_file')
    def test_reupload_uses_the_same_path_no_random_suffix(self, mock_upload):
        first = upload_invoice(self._file(), locker_id='RB-12345', parcel_display_id='RB-12345-P003')
        second = upload_invoice(self._file(), locker_id='RB-12345', parcel_display_id='RB-12345-P003')

        self.assertEqual(first, second)

    @patch('apps.accounts.services.SupabaseStorage.upload_file')
    def test_preserves_original_file_extension(self, mock_upload):
        path = upload_invoice(
            self._file(name='receipt.jpeg'), locker_id='RB-12345', parcel_display_id='RB-12345-P004',
        )

        self.assertTrue(path.endswith('invoice.jpeg'))
