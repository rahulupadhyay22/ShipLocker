"""Tests for Task 3 (Phase C — shipping / consolidation discount):

- Shipment.shipping_discount_amount / consolidation_fee_discount_amount properties
- Shipment.refresh_shipping_discount()
- apps.payments.services._get_consolidation_fee_amount(locker) / _lookup_consolidation_fee_standard()
- SelectShippingServiceView.post
- _get_service_options
- migration 0008 backfill RunPython step
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User, Locker
from apps.content.models import ShippingZone, ShippingRate, ServiceCharge
from apps.shipments.models import Shipment
from apps.shipments.views import _get_service_options
from apps.payments.services import _get_consolidation_fee_amount, _lookup_consolidation_fee_standard


def make_shipment(user, status='declaration_pending', **extra):
    defaults = dict(
        user=user,
        shipment_type='international',
        status=status,
        recipient_name='Jane Doe',
        recipient_phone='9999999999',
        address_line1='1 Test St',
        city='Testville',
        state='TS',
        postal_code='000000',
        country='USA',
    )
    defaults.update(extra)
    return Shipment.objects.create(**defaults)


def make_zone_and_rate(country='USA', service_type='standard', price=Decimal('1000.00')):
    zone = ShippingZone.objects.create(name='Zone A', countries=country, is_active=True)
    rate = ShippingRate.objects.create(
        zone=zone, service_type=service_type, min_weight=Decimal('0.00'), max_weight=Decimal('100.00'),
        rate_type='fixed', price=price, is_active=True,
    )
    return zone, rate


class ShipmentDiscountPropertyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='prop@example.com')

    def test_shipping_discount_amount_zero_when_standard_none(self):
        shipment = make_shipment(self.user, shipping_cost=Decimal('100.00'), shipping_cost_standard=None)
        self.assertEqual(shipment.shipping_discount_amount, Decimal('0.00'))

    def test_shipping_discount_amount_zero_when_cost_none(self):
        shipment = make_shipment(self.user, shipping_cost=None, shipping_cost_standard=Decimal('100.00'))
        self.assertEqual(shipment.shipping_discount_amount, Decimal('0.00'))

    def test_shipping_discount_amount_computed(self):
        shipment = make_shipment(self.user, shipping_cost=Decimal('95.00'), shipping_cost_standard=Decimal('100.00'))
        self.assertEqual(shipment.shipping_discount_amount, Decimal('5.00'))

    def test_consolidation_fee_discount_amount_zero_when_standard_none(self):
        shipment = make_shipment(self.user, consolidation_fee=Decimal('50.00'), consolidation_fee_standard=None)
        self.assertEqual(shipment.consolidation_fee_discount_amount, Decimal('0.00'))

    def test_consolidation_fee_discount_amount_zero_when_fee_none(self):
        shipment = make_shipment(self.user, consolidation_fee=None, consolidation_fee_standard=Decimal('50.00'))
        self.assertEqual(shipment.consolidation_fee_discount_amount, Decimal('0.00'))

    def test_consolidation_fee_discount_amount_computed(self):
        shipment = make_shipment(self.user, consolidation_fee=Decimal('0.00'), consolidation_fee_standard=Decimal('50.00'))
        self.assertEqual(shipment.consolidation_fee_discount_amount, Decimal('50.00'))


class RefreshShippingDiscountTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='refresh@example.com')
        self.locker = Locker.objects.create(user=self.user, plan_type='free')

    def test_noop_when_paid(self):
        shipment = make_shipment(
            self.user, payment_status='paid',
            shipping_cost=Decimal('100.00'), shipping_cost_standard=Decimal('100.00'),
        )
        self.locker.plan_type = 'paid'
        self.locker.save()
        shipment.refresh_shipping_discount()
        shipment.refresh_from_db()
        self.assertEqual(shipment.shipping_cost, Decimal('100.00'))

    def test_noop_when_standard_none(self):
        shipment = make_shipment(self.user, shipping_cost=None, shipping_cost_standard=None)
        shipment.refresh_shipping_discount()
        shipment.refresh_from_db()
        self.assertIsNone(shipment.shipping_cost)

    def test_free_to_premium_upgrade_drops_price_to_95_percent(self):
        shipment = make_shipment(
            self.user,
            shipping_cost=Decimal('100.00'), shipping_cost_standard=Decimal('100.00'),
        )
        # Upgrade to premium
        self.locker.plan_type = 'paid'
        self.locker.save()

        shipment.refresh_shipping_discount()
        shipment.refresh_from_db()
        self.assertEqual(shipment.shipping_cost, Decimal('95.00'))
        self.assertEqual(shipment.shipping_cost_standard, Decimal('100.00'))

    def test_paid_shipment_shipping_cost_unaffected_by_plan_change(self):
        shipment = make_shipment(
            self.user, payment_status='paid',
            shipping_cost=Decimal('100.00'), shipping_cost_standard=Decimal('100.00'),
        )
        self.locker.plan_type = 'paid'
        self.locker.save()

        shipment.refresh_shipping_discount()
        shipment.refresh_from_db()
        self.assertEqual(shipment.shipping_cost, Decimal('100.00'))


class ConsolidationFeeAmountTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='consol@example.com')
        self.free_locker = Locker.objects.create(user=self.user, plan_type='free')
        self.paid_user = User.objects.create(email='consol-paid@example.com')
        self.paid_locker = Locker.objects.create(user=self.paid_user, plan_type='paid')
        ServiceCharge.objects.create(
            code='consolidation_fee', name='Consolidation Fee',
            charge_type='flat', amount=Decimal('75.00'), is_active=True,
        )

    def test_no_args_raises_type_error(self):
        with self.assertRaises(TypeError):
            _get_consolidation_fee_amount()

    def test_premium_locker_returns_zero(self):
        self.assertEqual(_get_consolidation_fee_amount(self.paid_locker), Decimal('0.00'))

    def test_free_locker_returns_standard_amount(self):
        self.assertEqual(_get_consolidation_fee_amount(self.free_locker), Decimal('75.00'))

    def test_none_locker_returns_standard_amount(self):
        self.assertEqual(_get_consolidation_fee_amount(None), Decimal('75.00'))

    def test_lookup_standard_ignores_locker_plan(self):
        self.assertEqual(_lookup_consolidation_fee_standard(), Decimal('75.00'))


class SelectShippingServiceViewTests(TestCase):
    def setUp(self):
        self.free_user = User.objects.create(email='select-free@example.com')
        Locker.objects.create(user=self.free_user, plan_type='free')
        self.paid_user = User.objects.create(email='select-paid@example.com')
        Locker.objects.create(user=self.paid_user, plan_type='paid')
        make_zone_and_rate(price=Decimal('1000.00'))

    def _select(self, user, shipment):
        self.client.force_login(user)
        return self.client.post(
            reverse('shipments:select_service', kwargs={'pk': shipment.pk}),
            {'service_type': 'standard'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def test_free_user_shipping_cost_equals_standard(self):
        shipment = make_shipment(self.free_user, country='USA', total_weight_kg=Decimal('5.00'))
        response = self._select(self.free_user, shipment)
        self.assertEqual(response.status_code, 200)
        shipment.refresh_from_db()
        self.assertEqual(shipment.shipping_cost, Decimal('1000.00'))
        self.assertEqual(shipment.shipping_cost_standard, Decimal('1000.00'))

    def test_premium_user_shipping_cost_is_95_percent_of_standard(self):
        shipment = make_shipment(self.paid_user, country='USA', total_weight_kg=Decimal('5.00'))
        response = self._select(self.paid_user, shipment)
        self.assertEqual(response.status_code, 200)
        shipment.refresh_from_db()
        self.assertEqual(shipment.shipping_cost, Decimal('950.00'))
        self.assertEqual(shipment.shipping_cost_standard, Decimal('1000.00'))


class GetServiceOptionsTests(TestCase):
    def setUp(self):
        self.free_user = User.objects.create(email='opts-free@example.com')
        Locker.objects.create(user=self.free_user, plan_type='free')
        self.paid_user = User.objects.create(email='opts-paid@example.com')
        Locker.objects.create(user=self.paid_user, plan_type='paid')
        make_zone_and_rate(price=Decimal('1000.00'))

    def test_free_locker_price_equals_standard(self):
        shipment = make_shipment(self.free_user, country='USA', total_weight_kg=Decimal('5.00'))
        options = _get_service_options(shipment)
        self.assertEqual(len(options), 1)
        opt = options[0]
        self.assertEqual(opt['price'], Decimal('1000.00'))
        self.assertEqual(opt['standard_price'], Decimal('1000.00'))

    def test_premium_locker_price_is_95_percent_of_standard(self):
        shipment = make_shipment(self.paid_user, country='USA', total_weight_kg=Decimal('5.00'))
        options = _get_service_options(shipment)
        self.assertEqual(len(options), 1)
        opt = options[0]
        self.assertEqual(opt['standard_price'], Decimal('1000.00'))
        self.assertEqual(opt['price'], Decimal('950.00'))


class MigrationBackfillTests(TestCase):
    """Exercises the RunPython backfill function directly against real
    Shipment rows, rather than spinning up a full migration-history harness
    (no existing pattern for that in this repo)."""

    def test_backfill_function_sets_standard_fields_and_skips_nulls(self):
        import importlib
        migration_module = importlib.import_module(
            'apps.shipments.migrations.0008_shipment_consolidation_fee_standard_and_more'
        )

        user = User.objects.create(email='backfill@example.com')
        with_amounts = make_shipment(
            user, shipping_cost=Decimal('200.00'), consolidation_fee=Decimal('30.00'),
        )
        without_amounts = make_shipment(user, shipping_cost=None, consolidation_fee=None)

        # Simulate pre-migration state: standard fields unset (they default to
        # None already since the fields were just added).
        from django.apps import apps as django_apps
        migration_module.backfill_standard_amounts(django_apps, None)

        with_amounts.refresh_from_db()
        without_amounts.refresh_from_db()

        self.assertEqual(with_amounts.shipping_cost_standard, Decimal('200.00'))
        self.assertEqual(with_amounts.consolidation_fee_standard, Decimal('30.00'))
        self.assertIsNone(without_amounts.shipping_cost_standard)
        self.assertIsNone(without_amounts.consolidation_fee_standard)
