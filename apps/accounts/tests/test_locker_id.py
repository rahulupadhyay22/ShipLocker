"""
Tests for Trunk ID generation (spec: .claude/specs/06-trunk-id.md).

`generate_locker_id` moved from a random RB-##### format to the CamelTrunk
standard CT-HYD-###### format, generated with `secrets`. This locks down the
format and confirms the collision-retry loop still returns unique values.
"""
import re

from django.test import TestCase

from apps.accounts.models import Locker, User, generate_locker_id

TRUNK_ID_PATTERN = re.compile(r"^CT-HYD-\d{6}$")


class GenerateLockerIdTests(TestCase):
    def test_format(self):
        new_id = generate_locker_id()
        self.assertRegex(new_id, TRUNK_ID_PATTERN)

    def test_uniqueness_across_calls(self):
        ids = {generate_locker_id() for _ in range(20)}
        self.assertEqual(len(ids), 20)

    def test_locker_creation_uses_new_format(self):
        user = User.objects.create_user(email='trunkid@example.com')
        locker = Locker.objects.create(user=user)
        self.assertRegex(locker.locker_id, TRUNK_ID_PATTERN)


class ExistingLockerIdUntouchedTests(TestCase):
    """Spec: existing RB-##### lockers must not be migrated/regenerated."""

    def test_existing_rb_locker_id_not_regenerated_on_save(self):
        user = User.objects.create_user(email='legacy@example.com')
        locker = Locker.objects.create(user=user, locker_id='RB-38192')
        self.assertEqual(locker.locker_id, 'RB-38192')

        # Saving again (e.g. toggling is_active) must not touch locker_id,
        # since `default=generate_locker_id` only applies on first creation.
        locker.is_active = False
        locker.save()
        locker.refresh_from_db()
        self.assertEqual(locker.locker_id, 'RB-38192')

    def test_legacy_and_new_format_ids_coexist(self):
        legacy_user = User.objects.create_user(email='legacy2@example.com')
        legacy_locker = Locker.objects.create(user=legacy_user, locker_id='RB-99999')

        new_user = User.objects.create_user(email='newformat@example.com')
        new_locker = Locker.objects.create(user=new_user)

        self.assertEqual(legacy_locker.locker_id, 'RB-99999')
        self.assertRegex(new_locker.locker_id, TRUNK_ID_PATTERN)


class LockerStrTests(TestCase):
    """Spec: Locker.__str__ used in admin autocomplete must show Trunk ID + name."""

    def test_str_renders_locker_id_and_full_name(self):
        user = User.objects.create_user(email='strtest@example.com', full_name='Jane Doe')
        locker = Locker.objects.create(user=user, locker_id='CT-HYD-123456')
        self.assertEqual(str(locker), 'CT-HYD-123456 - Jane Doe')

    def test_str_falls_back_to_email_prefix_when_no_full_name(self):
        user = User.objects.create_user(email='nofullname@example.com')
        locker = Locker.objects.create(user=user, locker_id='CT-HYD-654321')
        self.assertEqual(str(locker), 'CT-HYD-654321 - nofullname')


class DisplayIdChainingTests(TestCase):
    """Spec: Parcel/Shipment display IDs derive from the new CT-HYD-XXXXXX locker_id."""

    def test_parcel_display_id_derives_from_new_locker_id(self):
        from apps.locker.models import Parcel

        user = User.objects.create_user(email='parcelchain@example.com')
        locker = Locker.objects.create(user=user, locker_id='CT-HYD-483921')
        parcel = Parcel.objects.create(locker=locker, item_name='Shoes', weight_kg='1.00')

        self.assertEqual(parcel.display_id, 'CT-HYD-483921-P001')

    def test_second_parcel_for_same_locker_increments_suffix(self):
        from apps.locker.models import Parcel

        user = User.objects.create_user(email='parcelchain2@example.com')
        locker = Locker.objects.create(user=user, locker_id='CT-HYD-111222')
        Parcel.objects.create(locker=locker, item_name='First', weight_kg='1.00')
        second = Parcel.objects.create(locker=locker, item_name='Second', weight_kg='2.00')

        self.assertEqual(second.display_id, 'CT-HYD-111222-P002')

    def test_shipment_display_id_derives_from_new_locker_id(self):
        from apps.shipments.models import Shipment

        user = User.objects.create_user(email='shipmentchain@example.com')
        Locker.objects.create(user=user, locker_id='CT-HYD-777888')
        shipment = Shipment.objects.create(
            user=user,
            shipment_type='international',
            recipient_name='Alice',
            recipient_phone='1234567890',
            address_line1='1 Main St',
            city='Metropolis',
            state='State',
            postal_code='00000',
        )

        self.assertEqual(shipment.display_id, 'CT-HYD-777888-S001')
