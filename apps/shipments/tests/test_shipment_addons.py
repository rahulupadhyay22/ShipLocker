"""Tests for shipment add-ons (spec:
docs/superpowers/specs/2026-08-31-shipment-addons-design.md)."""
from decimal import Decimal
from django.db import IntegrityError
from django.test import TestCase

from apps.accounts.models import User, Locker
from apps.shipments.models import Shipment, ShipmentAddon


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
