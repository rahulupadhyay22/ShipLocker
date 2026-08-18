"""ParcelImage rows must not orphan their Supabase Storage file on delete."""

from unittest.mock import patch

from django.test import TestCase

from apps.accounts.models import User, Locker
from apps.locker.models import Parcel, ParcelImage


class ParcelImageStorageCleanupTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(email='cleanup@example.com')
        self.locker = Locker.objects.create(user=user)
        self.parcel = Parcel.objects.create(locker=self.locker, item_name='Shoes', weight_kg='1.00')

    @patch('apps.accounts.services.SupabaseStorage.delete_file')
    def test_deleting_image_deletes_its_storage_file(self, mock_delete):
        image = ParcelImage.objects.create(parcel=self.parcel, image_path='RB-1/RB-1-P001/photo_abc123.jpg')

        image.delete()

        mock_delete.assert_called_once_with('parcel-images', 'RB-1/RB-1-P001/photo_abc123.jpg')

    @patch('apps.accounts.services.SupabaseStorage.delete_file')
    def test_deleting_parcel_cascades_to_its_images_storage_files(self, mock_delete):
        ParcelImage.objects.create(parcel=self.parcel, image_path='RB-1/RB-1-P001/primary_x.jpg')
        ParcelImage.objects.create(parcel=self.parcel, image_path='RB-1/RB-1-P001/photo_y.jpg')

        self.parcel.delete()

        self.assertEqual(mock_delete.call_count, 2)

    @patch('apps.accounts.services.SupabaseStorage.delete_file')
    def test_blank_image_path_does_not_call_storage(self, mock_delete):
        image = ParcelImage.objects.create(parcel=self.parcel, image_path='')

        image.delete()

        mock_delete.assert_not_called()

    @patch('apps.accounts.services.SupabaseStorage.delete_file', side_effect=Exception('storage down'))
    def test_storage_failure_does_not_block_the_db_delete(self, mock_delete):
        image = ParcelImage.objects.create(parcel=self.parcel, image_path='RB-1/RB-1-P001/photo_z.jpg')
        image_pk = image.pk

        image.delete()  # must not raise

        self.assertFalse(ParcelImage.objects.filter(pk=image_pk).exists())
