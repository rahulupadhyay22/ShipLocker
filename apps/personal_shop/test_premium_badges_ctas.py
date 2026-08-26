"""Tests for Task 6 (Phase F — badges, CTAs, checkout-linked templates)
covering personal_shop's TrunkAssist dashboard and quotation pages."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User, Locker
from .models import PersonalShopRequest, PersonalShopQuotation


def _make_locker(email, plan_type='free'):
    user = User.objects.create(email=email, is_active=True)
    return Locker.objects.create(user=user, plan_type=plan_type)


def _make_request(locker, status='submitted', **extra):
    return PersonalShopRequest.objects.create(
        locker=locker, request_type='custom_request', status=status, **extra,
    )


def _make_quotation(req, status='pending', valid_until=None, **extra):
    return PersonalShopQuotation.objects.create(
        request=req, total_amount=Decimal('100.00'), status=status,
        valid_until=valid_until or (timezone.now() + timedelta(hours=48)),
        **extra,
    )


class TrunkAssistDashboardIsPremiumContextTests(TestCase):
    def test_free_locker_is_premium_false(self):
        locker = _make_locker('ta-free@example.com', plan_type='free')
        self.client.force_login(locker.user)
        response = self.client.get(reverse('personal_shop:dashboard'))
        self.assertIn('is_premium', response.context)
        self.assertFalse(response.context['is_premium'])

    def test_premium_locker_is_premium_true(self):
        locker = _make_locker('ta-premium@example.com', plan_type='paid')
        self.client.force_login(locker.user)
        response = self.client.get(reverse('personal_shop:dashboard'))
        self.assertIn('is_premium', response.context)
        self.assertTrue(response.context['is_premium'])


class TrunkAssistDashboardRenderingTests(TestCase):
    def test_free_user_sees_premium_savings_note(self):
        locker = _make_locker('ta-free-render@example.com', plan_type='free')
        self.client.force_login(locker.user)
        response = self.client.get(reverse('personal_shop:dashboard'))
        self.assertContains(response, 'Premium saves 25% on service fees')

    def test_premium_user_does_not_see_premium_savings_note(self):
        locker = _make_locker('ta-premium-render@example.com', plan_type='paid')
        self.client.force_login(locker.user)
        response = self.client.get(reverse('personal_shop:dashboard'))
        self.assertNotContains(response, 'Premium saves 25% on service fees')


class QuotationPremiumDiscountRowTests(TestCase):
    def test_premium_discount_row_shown_when_amount_positive(self):
        locker = _make_locker('quote-premium@example.com', plan_type='paid')
        req = _make_request(locker, status='quotation_ready')
        quotation = _make_quotation(
            req, quotation_type='purchase',
            service_fee_standard_amount=Decimal('400.00'),
            service_fee_amount=Decimal('300.00'),
        )
        req.active_quotation = quotation
        req.save()
        self.client.force_login(locker.user)
        response = self.client.get(reverse('personal_shop:quotation_detail', args=[req.pk]))
        self.assertContains(response, 'Premium Discount')
        self.assertContains(response, '100.00')

    def test_free_locker_sees_premium_upsell_note_when_no_discount(self):
        locker = _make_locker('quote-free@example.com', plan_type='free')
        req = _make_request(locker, status='quotation_ready')
        quotation = _make_quotation(
            req, quotation_type='purchase',
            service_fee_standard_amount=Decimal('300.00'),
            service_fee_amount=Decimal('300.00'),
        )
        req.active_quotation = quotation
        req.save()
        self.client.force_login(locker.user)
        response = self.client.get(reverse('personal_shop:quotation_detail', args=[req.pk]))
        self.assertNotContains(response, 'Premium Discount')
        # Replaces the old inline "Premium saves 25% on this fee." row —
        # now a savings banner ("Save ₹X with Premium") with a checkout CTA.
        self.assertContains(response, 'ta-quote-savings-upsell')
        self.assertContains(response, 'Save ₹75.00 with Premium')

    def test_premium_locker_with_zero_discount_sees_no_upsell_note(self):
        # Edge case: an already-approved (locked, non-'pending') quotation
        # whose fee equals its standard amount — refresh_service_fee_discount
        # is a no-op here, so the discount stays at zero. A Premium locker in
        # this state should see neither the discount row nor the upsell note.
        locker = _make_locker('quote-premium-zero@example.com', plan_type='paid')
        req = _make_request(locker, status='paid')
        quotation = _make_quotation(
            req, quotation_type='purchase', status='approved',
            service_fee_standard_amount=Decimal('300.00'),
            service_fee_amount=Decimal('300.00'),
        )
        req.active_quotation = quotation
        req.save()
        self.client.force_login(locker.user)
        response = self.client.get(reverse('personal_shop:quotation_detail', args=[req.pk]))
        self.assertNotContains(response, 'Premium Discount')
        self.assertNotContains(response, 'ta-quote-savings-banner')
