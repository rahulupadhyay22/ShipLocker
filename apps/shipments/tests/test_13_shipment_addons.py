"""Tests for spec 13 (shipment add-ons + auto shipment-type), apps/shipments
scope: ShipmentAddon model, CreateShipmentView derivation/creation, auth/
ownership guards, ShipmentDetailView visibility, ShipmentAdmin inline.

Written independently of apps/shipments/tests/test_shipment_addons.py (an
earlier pass at the same feature) -- some overlap in what's asserted is
expected since both are derived from the same spec, but methods/scenarios
here are not copy-pasted from that file.
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib import admin
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User, Locker
from apps.content.models import ServiceCharge
from apps.content.services import invalidate_service_charge_cache
from apps.locker.models import Parcel
from apps.shipments.admin import ShipmentAddonInline
from apps.shipments.models import Shipment, ShipmentAddon


def make_shipment(user, **overrides):
    defaults = dict(
        user=user,
        shipment_type='international',
        status='pending_payment',
        recipient_name='Jane Doe',
        address_line1='1 Test Street',
        city='Testville',
        state='Test State',
        postal_code='12345',
        country='United States',
        currency='INR',
    )
    defaults.update(overrides)
    return Shipment.objects.create(**defaults)


def make_parcel(locker, **extra):
    defaults = dict(
        locker=locker, status='approved', item_name='Test Item',
        item_price=Decimal('2000.00'), category='electronics',
        customs_description='A test item', weight_kg=Decimal('1.0'),
    )
    defaults.update(extra)
    return Parcel.objects.create(**defaults)


# ---------------------------------------------------------------------------
# ShipmentAddon model
# ---------------------------------------------------------------------------

class ShipmentAddonModelBehaviorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='addon-behavior@example.com', is_active=True)
        Locker.objects.create(user=self.user)
        self.shipment = make_shipment(self.user)

    def test_amount_is_locked_in_and_not_recomputed_after_servicecharge_change(self):
        """Spec: 'amount is locked in at shipment creation (never recomputed
        later, even if the admin changes the ServiceCharge rate afterward).'"""
        addon = ShipmentAddon.objects.create(
            shipment=self.shipment, code='gift_wrapping', amount=Decimal('99.00'),
        )
        charge = ServiceCharge.objects.get(code='addon_gift_wrapping')
        charge.amount = Decimal('500.00')
        charge.save()
        invalidate_service_charge_cache('addon_gift_wrapping')
        self.addCleanup(invalidate_service_charge_cache, 'addon_gift_wrapping')

        addon.refresh_from_db()
        self.assertEqual(addon.amount, Decimal('99.00'))

    def test_same_code_allowed_across_different_shipments(self):
        other_shipment = make_shipment(self.user)
        ShipmentAddon.objects.create(shipment=self.shipment, code='insurance', amount=Decimal('99.00'))
        # Should not raise -- unique_together is scoped to (shipment, code).
        ShipmentAddon.objects.create(shipment=other_shipment, code='insurance', amount=Decimal('99.00'))
        self.assertEqual(ShipmentAddon.objects.filter(code='insurance').count(), 2)

    def test_duplicate_code_same_shipment_raises_integrity_error(self):
        ShipmentAddon.objects.create(shipment=self.shipment, code='insurance', amount=Decimal('99.00'))
        with self.assertRaises(IntegrityError):
            ShipmentAddon.objects.create(shipment=self.shipment, code='insurance', amount=Decimal('50.00'))

    def test_deleting_shipment_cascades_to_addons(self):
        ShipmentAddon.objects.create(shipment=self.shipment, code='extra_photos', amount=Decimal('149.00'))
        self.shipment.delete()
        self.assertEqual(ShipmentAddon.objects.count(), 0)

    def test_str_includes_addon_display_name_and_shipment_display_id(self):
        addon = ShipmentAddon.objects.create(shipment=self.shipment, code='priority_packing', amount=Decimal('299.00'))
        text = str(addon)
        self.assertIn('Priority Packing', text)
        self.assertIn(self.shipment.display_id, text)


# ---------------------------------------------------------------------------
# CreateShipmentView -- auth guard
# ---------------------------------------------------------------------------

class CreateShipmentViewAuthGuardTests(TestCase):
    def setUp(self):
        self.url = reverse('shipments:create')

    def test_get_unauthenticated_redirects_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url.lower())

    def test_post_unauthenticated_redirects_to_login(self):
        response = self.client.post(self.url, {'parcels': ['00000000-0000-0000-0000-000000000000']})
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url.lower())


# ---------------------------------------------------------------------------
# CreateShipmentView -- shipment_type derivation edge cases
# ---------------------------------------------------------------------------

class ShipmentTypeDerivationEdgeCaseTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='type-derivation@example.com', full_name='Signer Name', is_active=True)
        self.locker = Locker.objects.create(user=self.user)
        self.parcel = make_parcel(self.locker)
        self.client.force_login(self.user)
        self.url = reverse('shipments:create')

        self.generate_pdf_patcher = patch(
            'apps.shipments.services.declaration_service.DeclarationService.generate_pdf',
            return_value=b'%PDF-fake-bytes',
        )
        self.upload_pdf_patcher = patch(
            'apps.shipments.services.declaration_service.DeclarationService.upload_pdf',
            return_value='shipment/RB-00001/customs_fake.pdf',
        )
        self.generate_pdf_patcher.start()
        self.upload_pdf_patcher.start()
        self.addCleanup(self.generate_pdf_patcher.stop)
        self.addCleanup(self.upload_pdf_patcher.stop)

    def _valid_data(self, **overrides):
        data = {
            'parcels': [str(self.parcel.id)],
            'declaration_purpose': 'gift',
            'signature_agree': 'on',
            'signature_name': 'Signer Name',
            'recipient_name': 'Jane Doe',
            'recipient_phone': '9999999999',
            'recipient_email': 'jane@example.com',
            'address_line1': '1 Test Street',
            'address_line2': '',
            'city': 'Testville',
            'state': 'Test State',
            'postal_code': '123456',
            'country': 'United States',
        }
        data.update(overrides)
        return data

    def test_lowercase_india_derives_domestic(self):
        response = self.client.post(self.url, self._valid_data(country='india'))
        # A validation failure also redirects (back to shipments:create) --
        # assert we actually created the shipment, not just that we got a 302.
        self.assertEqual(Shipment.objects.count(), 1, response.context)
        shipment = Shipment.objects.get()
        self.assertRedirects(response, reverse('shipments:detail', kwargs={'pk': shipment.pk}))
        self.assertEqual(shipment.shipment_type, 'domestic')

    def test_mixed_case_india_with_whitespace_derives_domestic(self):
        response = self.client.post(self.url, self._valid_data(country='  InDiA  '))
        self.assertEqual(Shipment.objects.count(), 1)
        shipment = Shipment.objects.get()
        self.assertRedirects(response, reverse('shipments:detail', kwargs={'pk': shipment.pk}))
        self.assertEqual(shipment.shipment_type, 'domestic')

    def test_country_containing_india_as_substring_is_not_domestic(self):
        # Not an exact "INDIA" match -- must stay international.
        response = self.client.post(self.url, self._valid_data(country='British Indian Ocean Territory'))
        self.assertEqual(Shipment.objects.count(), 1)
        shipment = Shipment.objects.get()
        self.assertRedirects(response, reverse('shipments:detail', kwargs={'pk': shipment.pk}))
        self.assertEqual(shipment.shipment_type, 'international')


# ---------------------------------------------------------------------------
# CreateShipmentView -- server-side add-on creation, multi-select scenarios
# ---------------------------------------------------------------------------

class CreateShipmentAddonMultiSelectTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='addon-multiselect@example.com', full_name='Signer Two', is_active=True)
        self.locker = Locker.objects.create(user=self.user)
        # High-value parcel so insurance's 2% exceeds the 99 floor.
        self.parcel = make_parcel(self.locker, item_price=Decimal('10000.00'))
        self.client.force_login(self.user)
        self.url = reverse('shipments:create')

        self.generate_pdf_patcher = patch(
            'apps.shipments.services.declaration_service.DeclarationService.generate_pdf',
            return_value=b'%PDF-fake-bytes',
        )
        self.upload_pdf_patcher = patch(
            'apps.shipments.services.declaration_service.DeclarationService.upload_pdf',
            return_value='shipment/RB-00001/customs_fake.pdf',
        )
        self.generate_pdf_patcher.start()
        self.upload_pdf_patcher.start()
        self.addCleanup(self.generate_pdf_patcher.stop)
        self.addCleanup(self.upload_pdf_patcher.stop)

    def _valid_data(self, **overrides):
        data = {
            'parcels': [str(self.parcel.id)],
            'declaration_purpose': 'gift',
            'signature_agree': 'on',
            'signature_name': 'Signer Two',
            'recipient_name': 'Jane Doe',
            'recipient_phone': '9999999999',
            'recipient_email': 'jane@example.com',
            'address_line1': '1 Test Street',
            'address_line2': '',
            'city': 'Testville',
            'state': 'Test State',
            'postal_code': '123456',
            'country': 'United States',
        }
        data.update(overrides)
        return data

    def test_all_four_addons_selected_creates_four_rows(self):
        response = self.client.post(self.url, self._valid_data(
            addons=['insurance', 'extra_photos', 'priority_packing', 'gift_wrapping'],
        ))
        self.assertEqual(Shipment.objects.count(), 1, response.context)
        shipment = Shipment.objects.get()
        self.assertRedirects(response, reverse('shipments:detail', kwargs={'pk': shipment.pk}))
        self.assertEqual(shipment.addons.count(), 4)

    def test_insurance_uses_percentage_when_above_floor(self):
        # item_price=10000 -> 2% = 200.00, above the 99 floor.
        response = self.client.post(self.url, self._valid_data(addons=['insurance']))
        self.assertEqual(Shipment.objects.count(), 1, response.context)
        shipment = Shipment.objects.get()
        self.assertRedirects(response, reverse('shipments:detail', kwargs={'pk': shipment.pk}))
        addon = shipment.addons.get(code='insurance')
        self.assertEqual(addon.amount, Decimal('200.00'))

    def test_duplicate_addon_value_in_post_creates_only_one_row(self):
        response = self.client.post(self.url, self._valid_data(addons=['gift_wrapping', 'gift_wrapping']))
        self.assertEqual(Shipment.objects.count(), 1, response.context)
        shipment = Shipment.objects.get()
        self.assertRedirects(response, reverse('shipments:detail', kwargs={'pk': shipment.pk}))
        self.assertEqual(shipment.addons.filter(code='gift_wrapping').count(), 1)

    def test_mix_of_known_and_unknown_addon_codes_only_creates_known_ones(self):
        response = self.client.post(self.url, self._valid_data(
            addons=['gift_wrapping', 'bogus_addon', 'priority_packing'],
        ))
        self.assertEqual(Shipment.objects.count(), 1, response.context)
        shipment = Shipment.objects.get()
        self.assertRedirects(response, reverse('shipments:detail', kwargs={'pk': shipment.pk}))
        codes = set(shipment.addons.values_list('code', flat=True))
        self.assertEqual(codes, {'gift_wrapping', 'priority_packing'})


# ---------------------------------------------------------------------------
# ShipmentDetailView -- add-ons + insurance visibility, ownership guard
# ---------------------------------------------------------------------------

class ShipmentDetailAddonVisibilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='detail-visibility@example.com', is_active=True)
        Locker.objects.create(user=self.user)
        self.shipment = make_shipment(self.user)
        self.client.force_login(self.user)
        self.url = reverse('shipments:detail', kwargs={'pk': self.shipment.pk})

    def test_has_insurance_addon_false_by_default(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['has_insurance_addon'])

    def test_has_insurance_addon_true_when_purchased(self):
        ShipmentAddon.objects.create(shipment=self.shipment, code='insurance', amount=Decimal('99.00'))
        response = self.client.get(self.url)
        self.assertTrue(response.context['has_insurance_addon'])

    def test_has_insurance_addon_false_when_only_other_addons_purchased(self):
        ShipmentAddon.objects.create(shipment=self.shipment, code='gift_wrapping', amount=Decimal('99.00'))
        response = self.client.get(self.url)
        self.assertFalse(response.context['has_insurance_addon'])


class ShipmentDetailOwnershipGuardTests(TestCase):
    """Spec: 'A different user cannot see or influence another user's
    shipment/add-on selection (existing Shipment ownership scoping...).'
    ShipmentDetailView scopes its queryset by user=request.user, so a
    mismatched owner must 404, not reveal the shipment or 403."""

    def setUp(self):
        self.owner = User.objects.create(email='owner-detail@example.com', is_active=True)
        self.intruder = User.objects.create(email='intruder-detail@example.com', is_active=True)
        Locker.objects.create(user=self.owner)
        Locker.objects.create(user=self.intruder)
        self.shipment = make_shipment(self.owner)
        ShipmentAddon.objects.create(shipment=self.shipment, code='gift_wrapping', amount=Decimal('99.00'))
        self.url = reverse('shipments:detail', kwargs={'pk': self.shipment.pk})

    def test_other_user_gets_404_not_403_or_200(self):
        self.client.force_login(self.intruder)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_redirects_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url.lower())

    def test_owner_can_see_their_own_shipment(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# ShipmentAdmin -- staff visibility of purchased add-ons
# ---------------------------------------------------------------------------

class ShipmentAdminAddonInlineTests(TestCase):
    def test_inline_is_registered_on_shipment_admin(self):
        model_admin = admin.site._registry[Shipment]
        self.assertIn(ShipmentAddonInline, model_admin.inlines)

    def test_inline_is_read_only(self):
        inline = ShipmentAddonInline(Shipment, admin.site)
        self.assertEqual(set(inline.readonly_fields), {'code', 'amount', 'created_at'})
        self.assertFalse(inline.has_add_permission(request=None))

    def test_inline_model_is_shipment_addon(self):
        inline = ShipmentAddonInline(Shipment, admin.site)
        self.assertIs(inline.model, ShipmentAddon)
