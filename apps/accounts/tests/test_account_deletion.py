import json
from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.account_deletion import DeletionBlocked, delete_user_account
from apps.accounts.models import KYCDocument, Locker, SavedAddress, User
from apps.locker.models import Batch, Parcel
from apps.payments.models import BatchCharge, Payment
from apps.shipments.models import Shipment, ShipmentItem


def _make_user_with_locker(email='deleteme@example.com'):
    user = User.objects.create(
        email=email, full_name='Delete Me', phone='+911234567890',
        supabase_id=f'sb-{email}',
    )
    locker = Locker.objects.create(user=user)
    return user, locker


class DeleteUserAccountTests(TestCase):
    def test_kyc_documents_and_addresses_are_hard_deleted(self):
        user, locker = _make_user_with_locker()
        KYCDocument.objects.create(user=user, document_type='aadhaar', document_url='kyc/x.jpg')
        SavedAddress.objects.create(
            user=user, recipient_name='X', recipient_phone='+911234567890',
            address_line1='addr', city='Hyderabad', state='TS', postal_code='500001',
        )

        delete_user_account(user)

        self.assertEqual(KYCDocument.objects.filter(user=user).count(), 0)
        self.assertEqual(SavedAddress.objects.filter(user=user).count(), 0)

    def test_never_shipped_parcel_is_deleted_but_shipped_parcel_is_retained(self):
        user, locker = _make_user_with_locker()
        never_shipped = Parcel.objects.create(locker=locker, item_name='never shipped')
        shipped = Parcel.objects.create(locker=locker, item_name='shipped')
        shipment = Shipment.objects.create(
            user=user, shipment_type='international', recipient_name='R', recipient_phone='1',
            address_line1='a', city='c', state='s', postal_code='1',
        )
        ShipmentItem.objects.create(shipment=shipment, parcel=shipped)

        delete_user_account(user)

        self.assertFalse(Parcel.objects.filter(pk=never_shipped.pk).exists())
        self.assertTrue(Parcel.objects.filter(pk=shipped.pk).exists())
        self.assertTrue(ShipmentItem.objects.filter(parcel=shipped).exists())

    def test_user_row_is_anonymized_not_deleted(self):
        user, locker = _make_user_with_locker()
        original_pk = user.pk

        delete_user_account(user)
        user.refresh_from_db()

        self.assertEqual(user.pk, original_pk)
        self.assertNotEqual(user.email, 'deleteme@example.com')
        self.assertIn('deleted-', user.email)
        self.assertEqual(user.full_name, '')
        self.assertEqual(user.phone, '')
        self.assertFalse(user.is_active)
        self.assertIsNone(user.supabase_id)
        self.assertIsNotNone(user.anonymized_at)

    def test_payment_and_batch_charge_survive(self):
        user, locker = _make_user_with_locker()
        Payment.objects.create(user=user, amount=100, payment_type='shipment', status='captured')
        batch = Batch.objects.create(
            locker=locker, plan_type_at_creation='free', quota_year=2026,
            first_parcel_received_date=date.today(),
        )
        BatchCharge.objects.create(
            batch=batch, charge_date=date.today(), parcel_count_snapshot=1,
            amount=50, status='paid',
        )

        delete_user_account(user)

        self.assertEqual(Payment.objects.filter(user=user).count(), 1)
        self.assertEqual(BatchCharge.objects.filter(batch=batch).count(), 1)

    def test_blocked_when_batch_charge_unpaid(self):
        user, locker = _make_user_with_locker()
        batch = Batch.objects.create(
            locker=locker, plan_type_at_creation='free', quota_year=2026,
            first_parcel_received_date=date.today(),
        )
        BatchCharge.objects.create(
            batch=batch, charge_date=date.today(), parcel_count_snapshot=1,
            amount=50, status='pending',
        )

        with self.assertRaises(DeletionBlocked):
            delete_user_account(user)

        user.refresh_from_db()
        self.assertEqual(user.email, 'deleteme@example.com')  # untouched


class AccountDeletionRequestViewTests(TestCase):
    def test_get_requires_login(self):
        response = self.client.get(reverse('accounts:delete_account'))
        self.assertNotEqual(response.status_code, 200)

    def test_post_without_confirmation_text_does_not_delete(self):
        user, locker = _make_user_with_locker('novconfirm@example.com')
        self.client.force_login(user)

        response = self.client.post(reverse('accounts:delete_account'), {'confirm': 'nope'})

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.email, 'novconfirm@example.com')

    def test_post_with_confirmation_deletes_and_logs_out(self):
        user, locker = _make_user_with_locker('confirmed@example.com')
        self.client.force_login(user)

        response = self.client.post(reverse('accounts:delete_account'), {'confirm': 'DELETE'})

        self.assertRedirects(response, reverse('content:home'))
        user.refresh_from_db()
        self.assertNotEqual(user.email, 'confirmed@example.com')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_blocked_deletion_shows_message_and_keeps_user(self):
        user, locker = _make_user_with_locker('blocked@example.com')
        batch = Batch.objects.create(
            locker=locker, plan_type_at_creation='free', quota_year=2026,
            first_parcel_received_date=date.today(),
        )
        BatchCharge.objects.create(
            batch=batch, charge_date=date.today(), parcel_count_snapshot=1,
            amount=50, status='pending',
        )
        self.client.force_login(user)

        response = self.client.post(reverse('accounts:delete_account'), {'confirm': 'DELETE'})

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.email, 'blocked@example.com')


class AccountDataExportViewTests(TestCase):
    def test_export_scoped_to_requesting_user_only(self):
        user, locker = _make_user_with_locker('exportme@example.com')
        Parcel.objects.create(locker=locker, item_name='mine')
        other_user, other_locker = _make_user_with_locker('other@example.com')
        Parcel.objects.create(locker=other_locker, item_name='not mine')
        self.client.force_login(user)

        response = self.client.get(reverse('accounts:data_export'))

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['profile']['email'], 'exportme@example.com')
        self.assertEqual(len(data['parcels']), 1)
        self.assertEqual(data['parcels'][0]['item_name'], 'mine')

    def test_requires_login(self):
        response = self.client.get(reverse('accounts:data_export'))
        self.assertNotEqual(response.status_code, 200)
