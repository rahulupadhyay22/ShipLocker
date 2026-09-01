"""Tests for shipment add-ons (spec:
docs/superpowers/specs/2026-08-31-shipment-addons-design.md)."""
from decimal import Decimal
from unittest.mock import patch
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User, Locker
from apps.locker.models import Parcel
from apps.shipments.models import Shipment, ShipmentAddon
from apps.shipments.views import _payment_summary


def make_shipment(user):
    return Shipment.objects.create(
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


class ShipmentAddonModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='addon-model@example.com', is_active=True)
        Locker.objects.create(user=self.user)
        self.shipment = make_shipment(self.user)

    def test_creates_addon_with_valid_code(self):
        addon = ShipmentAddon.objects.create(
            shipment=self.shipment, code='insurance', amount=Decimal('99.00'),
        )
        self.assertEqual(self.shipment.addons.count(), 1)
        self.assertEqual(addon.amount, Decimal('99.00'))

    def test_duplicate_code_on_same_shipment_rejected(self):
        ShipmentAddon.objects.create(
            shipment=self.shipment, code='gift_wrapping', amount=Decimal('99.00'),
        )
        with self.assertRaises(IntegrityError):
            ShipmentAddon.objects.create(
                shipment=self.shipment, code='gift_wrapping', amount=Decimal('99.00'),
            )


def make_parcel(locker, **extra):
    defaults = dict(
        locker=locker, status='approved', item_name='Test Item',
        item_price=Decimal('2000.00'), category='electronics',
        customs_description='A test item', weight_kg=Decimal('1.0'),
    )
    defaults.update(extra)
    return Parcel.objects.create(**defaults)


class CreateShipmentAddonsAndTypeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='create-addons@example.com', full_name='Rahul Signer', is_active=True)
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
            'signature_name': 'Rahul Signer',
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

    def test_get_context_includes_addon_options(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        codes = {opt['code'] for opt in response.context['addon_options']}
        self.assertEqual(codes, {'insurance', 'extra_photos', 'priority_packing', 'gift_wrapping'})

    def test_india_country_derives_domestic(self):
        response = self.client.post(self.url, self._valid_data(country='India'))
        self.assertEqual(response.status_code, 302)
        shipment = Shipment.objects.get()
        self.assertEqual(shipment.shipment_type, 'domestic')

    def test_non_india_country_derives_international(self):
        response = self.client.post(self.url, self._valid_data(country='United States'))
        self.assertEqual(response.status_code, 302)
        shipment = Shipment.objects.get()
        self.assertEqual(shipment.shipment_type, 'international')

    def test_stray_shipment_type_post_field_is_ignored(self):
        # A client-supplied shipment_type must never override the
        # country-derived value.
        response = self.client.post(self.url, self._valid_data(country='India', shipment_type='international'))
        self.assertEqual(response.status_code, 302)
        shipment = Shipment.objects.get()
        self.assertEqual(shipment.shipment_type, 'domestic')

    def test_selected_addons_create_shipmentaddon_rows_with_server_computed_amounts(self):
        response = self.client.post(self.url, self._valid_data(addons=['gift_wrapping', 'priority_packing']))
        self.assertEqual(response.status_code, 302)
        shipment = Shipment.objects.get()
        addons = {a.code: a.amount for a in shipment.addons.all()}
        self.assertEqual(addons, {'gift_wrapping': Decimal('99.00'), 'priority_packing': Decimal('299.00')})

    def test_client_supplied_addon_amount_is_ignored(self):
        # Only 'addons' (the code list) is a real form field; there is no
        # amount field for the client to tamper with in the first place --
        # this test documents/locks in that the server always recomputes.
        response = self.client.post(self.url, self._valid_data(addons=['insurance']))
        self.assertEqual(response.status_code, 302)
        shipment = Shipment.objects.get()
        addon = shipment.addons.get(code='insurance')
        # self.parcel has item_price=2000.00 -> 2% = 40.00, below the 99.00 floor
        self.assertEqual(addon.amount, Decimal('99.00'))

    def test_unknown_addon_code_is_ignored(self):
        response = self.client.post(self.url, self._valid_data(addons=['not_a_real_addon']))
        self.assertEqual(response.status_code, 302)
        shipment = Shipment.objects.get()
        self.assertEqual(shipment.addons.count(), 0)

    def test_unconfigured_addon_creates_no_row_even_if_requested(self):
        from apps.content.models import ServiceCharge
        from apps.content.services import invalidate_service_charge_cache
        ServiceCharge.objects.filter(code='addon_extra_photos').update(is_active=False)
        invalidate_service_charge_cache('addon_extra_photos')
        self.addCleanup(invalidate_service_charge_cache, 'addon_extra_photos')

        response = self.client.post(self.url, self._valid_data(addons=['extra_photos']))
        self.assertEqual(response.status_code, 302)
        shipment = Shipment.objects.get()
        self.assertEqual(shipment.addons.count(), 0)

    def test_no_addons_selected_creates_no_rows(self):
        response = self.client.post(self.url, self._valid_data())
        self.assertEqual(response.status_code, 302)
        shipment = Shipment.objects.get()
        self.assertEqual(shipment.addons.count(), 0)


class CreateShipmentTemplateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='create-template@example.com', is_active=True)
        self.locker = Locker.objects.create(user=self.user)
        self.parcel = make_parcel(self.locker)
        self.client.force_login(self.user)

    def test_no_shipment_type_radio_rendered(self):
        response = self.client.get(reverse('shipments:create'))
        self.assertNotContains(response, 'name="shipment_type"')

    def test_addon_checkboxes_rendered_for_each_configured_addon(self):
        response = self.client.get(reverse('shipments:create'))
        for code in ('insurance', 'extra_photos', 'priority_packing', 'gift_wrapping'):
            self.assertContains(response, f'value="{code}"')

    def test_parcel_card_carries_item_price_data_attribute(self):
        response = self.client.get(reverse('shipments:create'))
        self.assertContains(response, 'data-item-price=')

    def test_hidden_addon_not_rendered(self):
        from apps.content.models import ServiceCharge
        from apps.content.services import invalidate_service_charge_cache
        ServiceCharge.objects.filter(code='addon_insurance').update(is_active=False)
        invalidate_service_charge_cache('addon_insurance')
        self.addCleanup(invalidate_service_charge_cache, 'addon_insurance')

        response = self.client.get(reverse('shipments:create'))
        self.assertNotContains(response, 'value="insurance"')


class PaymentSummaryAddonsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='payment-summary-addons@example.com', is_active=True)
        Locker.objects.create(user=self.user, plan_type='free')
        self.shipment = make_shipment(self.user)
        self.shipment.shipping_cost = Decimal('800.00')
        self.shipment.consolidation_fee = Decimal('300.00')
        self.shipment.payment_status = 'unpaid'
        self.shipment.save()
        ShipmentAddon.objects.create(shipment=self.shipment, code='gift_wrapping', amount=Decimal('99.00'))
        ShipmentAddon.objects.create(shipment=self.shipment, code='priority_packing', amount=Decimal('299.00'))

    def test_addons_amount_included_in_unpaid_charges_and_amount_due(self):
        summary = _payment_summary(self.shipment)
        self.assertEqual(summary['addons_amount'], Decimal('398.00'))
        # 800 shipping + 300 consolidation + 398 addons = 1498.00
        self.assertEqual(summary['shipment_amount_due'], Decimal('1498.00'))


class ShipmentDetailAddonsVisibilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='detail-addons@example.com', is_active=True)
        Locker.objects.create(user=self.user)
        self.shipment = make_shipment(self.user)
        self.client.force_login(self.user)
        self.url = reverse('shipments:detail', kwargs={'pk': self.shipment.pk})

    def test_no_addons_section_hidden(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Gift Wrapping')

    def test_purchased_addons_are_listed(self):
        ShipmentAddon.objects.create(shipment=self.shipment, code='gift_wrapping', amount=Decimal('99.00'))
        ShipmentAddon.objects.create(shipment=self.shipment, code='insurance', amount=Decimal('120.00'))
        response = self.client.get(self.url)
        self.assertContains(response, 'Gift Wrapping')
        self.assertContains(response, 'Insurance')
        self.assertContains(response, '99.00')
        self.assertContains(response, '120.00')
