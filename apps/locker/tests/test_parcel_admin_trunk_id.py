"""
Tests for ParcelAdmin Trunk ID / customer name display (spec: .claude/specs/06-trunk-id.md).

The spec requires warehouse staff to see Customer Name + Trunk ID together in
the admin parcel list/detail screens, and to be able to search parcels by
Trunk ID. These are plain ModelAdmin callables/config, not HTTP views, so
they're exercised directly against a `ParcelAdmin` instance rather than via
the test client (this surface has no ownership mixin — it's an
admin-staff-only surface, not a user-facing authenticated view).

Also locks down a real regression caught this session: `trunk_id(None)` /
`customer_name(None)` used to crash on the Django admin "Add Parcel" page
(where `obj` is `None` before first save) instead of returning '-'.
"""
from django.contrib import admin
from django.test import TestCase

from apps.accounts.models import User, Locker
from apps.locker.admin import ParcelAdmin
from apps.locker.models import Parcel


class ParcelAdminTrunkIdTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='admintrunk@example.com', full_name='Jane Doe')
        self.locker = Locker.objects.create(user=self.user, locker_id='CT-HYD-483921')
        self.parcel = Parcel.objects.create(
            locker=self.locker, item_name='Sneakers', weight_kg='1.50',
        )
        self.admin = ParcelAdmin(Parcel, admin.site)

    def test_trunk_id_returns_locker_locker_id(self):
        self.assertEqual(self.admin.trunk_id(self.parcel), 'CT-HYD-483921')

    def test_customer_name_returns_user_full_name(self):
        self.assertEqual(self.admin.customer_name(self.parcel), 'Jane Doe')

    def test_trunk_id_none_obj_returns_dash_not_crash(self):
        """Regression: Add Parcel page passes obj=None before first save."""
        self.assertEqual(self.admin.trunk_id(None), '-')

    def test_customer_name_none_obj_returns_dash_not_crash(self):
        """Regression: Add Parcel page passes obj=None before first save."""
        self.assertEqual(self.admin.customer_name(None), '-')

    def test_search_fields_includes_locker_locker_id(self):
        self.assertIn('locker__locker_id', self.admin.search_fields)

    def test_list_display_includes_trunk_id_and_customer_name(self):
        self.assertIn('trunk_id', self.admin.list_display)
        self.assertIn('customer_name', self.admin.list_display)

    def test_customer_name_falls_back_to_email_prefix_when_no_full_name(self):
        user2 = User.objects.create_user(email='noname@example.com')
        locker2 = Locker.objects.create(user=user2, locker_id='CT-HYD-111222')
        parcel2 = Parcel.objects.create(locker=locker2, item_name='Bag', weight_kg='0.50')

        self.assertEqual(self.admin.customer_name(parcel2), 'noname')
