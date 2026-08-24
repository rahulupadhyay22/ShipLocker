"""Tests for Task 6 (Phase F — badges, CTAs, checkout-linked templates)
covering the shipment detail page's Premium savings notes/strikethroughs."""
import re
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User, Locker
from apps.content.models import ShippingZone, ShippingRate, ServiceCharge
from apps.shipments.models import Shipment


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


class ShipmentDetailPremiumRenderingTests(TestCase):
    def setUp(self):
        make_zone_and_rate(price=Decimal('1000.00'))
        ServiceCharge.objects.create(
            code='consolidation_fee', name='Consolidation Fee',
            charge_type='flat', amount=Decimal('75.00'), is_active=True,
        )
        self.free_user = User.objects.create(email='detail-free@example.com')
        Locker.objects.create(user=self.free_user, plan_type='free')
        self.premium_user = User.objects.create(email='detail-premium@example.com')
        Locker.objects.create(user=self.premium_user, plan_type='paid')

    def test_free_user_sees_no_premium_shipping_note_and_no_free_consolidation(self):
        shipment = make_shipment(
            self.free_user, total_weight_kg=Decimal('5.00'), payment_status='paid',
            shipping_cost=Decimal('1000.00'), shipping_cost_standard=Decimal('1000.00'),
            consolidation_fee=Decimal('75.00'), consolidation_fee_standard=Decimal('75.00'),
        )
        self.client.force_login(self.free_user)
        response = self.client.get(reverse('shipments:detail', args=[shipment.pk]))
        self.assertFalse(response.context['is_premium'])
        self.assertNotContains(response, 'Premium savings applied')
        self.assertNotContains(response, 'FREE with Premium')

    def test_premium_user_sees_shipping_discount_note(self):
        shipment = make_shipment(
            self.premium_user, total_weight_kg=Decimal('5.00'), payment_status='paid',
            shipping_cost=Decimal('950.00'), shipping_cost_standard=Decimal('1000.00'),
            consolidation_fee=Decimal('0.00'), consolidation_fee_standard=Decimal('75.00'),
        )
        self.client.force_login(self.premium_user)
        response = self.client.get(reverse('shipments:detail', args=[shipment.pk]))
        self.assertTrue(response.context['is_premium'])
        self.assertContains(response, '(5% Premium savings applied)')

    def test_premium_user_sees_free_consolidation_note_with_savings(self):
        shipment = make_shipment(
            self.premium_user, total_weight_kg=Decimal('5.00'), payment_status='paid',
            shipping_cost=Decimal('950.00'), shipping_cost_standard=Decimal('1000.00'),
            consolidation_fee=Decimal('0.00'), consolidation_fee_standard=Decimal('75.00'),
        )
        self.client.force_login(self.premium_user)
        response = self.client.get(reverse('shipments:detail', args=[shipment.pk]))
        self.assertContains(response, 'FREE with Premium')
        self.assertContains(response, '75.00')

    def test_free_user_service_options_show_no_strikethrough(self):
        shipment = make_shipment(self.free_user, total_weight_kg=Decimal('5.00'))
        self.client.force_login(self.free_user)
        response = self.client.get(reverse('shipments:detail', args=[shipment.pk]))
        self.assertNotContains(response, 'sd-service-price-strike')

    def test_premium_user_service_options_show_strikethrough_standard_price(self):
        shipment = make_shipment(self.premium_user, total_weight_kg=Decimal('5.00'))
        self.client.force_login(self.premium_user)
        response = self.client.get(reverse('shipments:detail', args=[shipment.pk]))
        self.assertContains(response, 'sd-service-price-strike')
        self.assertContains(response, '1000.00')  # standard price struck through
        self.assertContains(response, '950.00')  # discounted price shown

    def test_consolidation_row_hidden_when_no_fee_and_no_standard(self):
        # Zero consolidation_fee_standard means nothing to show at all.
        shipment = make_shipment(
            self.free_user, total_weight_kg=Decimal('5.00'), payment_status='paid',
            consolidation_fee=Decimal('0.00'), consolidation_fee_standard=Decimal('0.00'),
        )
        self.client.force_login(self.free_user)
        response = self.client.get(reverse('shipments:detail', args=[shipment.pk]))
        self.assertNotContains(response, 'FREE with Premium')
        html = response.content.decode()
        self.assertRegex(html, r'id="sd-consolidation-fee-row"\s*hidden')
