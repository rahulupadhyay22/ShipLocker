"""Tests for Task 6 (Phase F — badges, CTAs, checkout-linked templates):

- DashboardView / ProfileView context now include 'is_premium'.
- Dashboard and profile pages render the FREE/PREMIUM badge and the
  upgrade/renew CTAs correctly for both plan states.
"""
from datetime import timedelta

from django.template.defaultfilters import date as date_filter
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User, Locker


def _make_user_with_locker(email, plan_type='free', premium_expires_at=None):
    user = User.objects.create(email=email, is_active=True)
    locker = Locker.objects.create(user=user, plan_type=plan_type, premium_expires_at=premium_expires_at)
    return user, locker


class DashboardIsPremiumContextTests(TestCase):
    def test_free_locker_context_is_premium_false(self):
        user, _ = _make_user_with_locker('free-dash@example.com', plan_type='free')
        self.client.force_login(user)
        response = self.client.get(reverse('accounts:dashboard'))
        self.assertIn('is_premium', response.context)
        self.assertFalse(response.context['is_premium'])

    def test_premium_locker_context_is_premium_true(self):
        user, _ = _make_user_with_locker(
            'premium-dash@example.com', plan_type='paid',
            premium_expires_at=timezone.localdate() + timedelta(days=200),
        )
        self.client.force_login(user)
        response = self.client.get(reverse('accounts:dashboard'))
        self.assertIn('is_premium', response.context)
        self.assertTrue(response.context['is_premium'])


class DashboardBadgeCtaRenderingTests(TestCase):
    def test_free_user_sees_free_badge_and_upgrade_banner(self):
        user, _ = _make_user_with_locker('free-render@example.com', plan_type='free')
        self.client.force_login(user)
        response = self.client.get(reverse('accounts:dashboard'))
        self.assertContains(response, 'FREE')
        self.assertContains(response, 'Upgrade to Premium')
        self.assertContains(response, '₹2,999/year')
        self.assertNotContains(response, 'status-premium')

    def test_premium_user_sees_premium_badge_no_upgrade_banner(self):
        user, _ = _make_user_with_locker(
            'premium-render@example.com', plan_type='paid',
            premium_expires_at=timezone.localdate() + timedelta(days=200),
        )
        self.client.force_login(user)
        response = self.client.get(reverse('accounts:dashboard'))
        self.assertContains(response, 'PREMIUM')
        self.assertContains(response, 'status-premium')
        self.assertNotContains(response, 'Upgrade to Premium')


class ProfileIsPremiumContextTests(TestCase):
    def test_free_locker_context_is_premium_false(self):
        user, _ = _make_user_with_locker('free-profile@example.com', plan_type='free')
        self.client.force_login(user)
        response = self.client.get(reverse('accounts:profile'))
        self.assertIn('is_premium', response.context)
        self.assertFalse(response.context['is_premium'])

    def test_premium_locker_context_is_premium_true(self):
        user, _ = _make_user_with_locker(
            'premium-profile@example.com', plan_type='paid',
            premium_expires_at=timezone.localdate() + timedelta(days=200),
        )
        self.client.force_login(user)
        response = self.client.get(reverse('accounts:profile'))
        self.assertIn('is_premium', response.context)
        self.assertTrue(response.context['is_premium'])


class ProfileBadgeCtaRenderingTests(TestCase):
    def test_free_user_sees_free_badge_and_upgrade_cta(self):
        user, _ = _make_user_with_locker('free-profile-render@example.com', plan_type='free')
        self.client.force_login(user)
        response = self.client.get(reverse('accounts:profile'))
        self.assertContains(response, 'FREE')
        self.assertContains(response, 'Upgrade to Premium')
        self.assertNotContains(response, 'Renew now')

    def test_premium_user_far_from_expiry_sees_no_renew_cta(self):
        expiry = timezone.localdate() + timedelta(days=200)
        user, _ = _make_user_with_locker(
            'premium-profile-far@example.com', plan_type='paid', premium_expires_at=expiry,
        )
        self.client.force_login(user)
        response = self.client.get(reverse('accounts:profile'))
        self.assertContains(response, 'PREMIUM')
        self.assertContains(response, date_filter(expiry))
        self.assertNotContains(response, 'Renew now')
        self.assertNotContains(response, 'Upgrade to Premium')

    def test_premium_user_near_expiry_sees_renew_cta(self):
        expiry = timezone.localdate() + timedelta(days=3)
        user, _ = _make_user_with_locker(
            'premium-profile-render@example.com', plan_type='paid', premium_expires_at=expiry,
        )
        self.client.force_login(user)
        response = self.client.get(reverse('accounts:profile'))
        self.assertContains(response, 'PREMIUM')
        self.assertContains(response, 'Renew now')
        self.assertContains(response, date_filter(expiry))
        self.assertNotContains(response, 'Upgrade to Premium')

    def test_premium_checkout_script_included(self):
        user, _ = _make_user_with_locker(
            'checkout-script@example.com', plan_type='paid',
            premium_expires_at=timezone.localdate() + timedelta(days=3),
        )
        self.client.force_login(user)
        response = self.client.get(reverse('accounts:profile'))
        self.assertContains(response, 'premium-checkout-btn')
        self.assertContains(response, reverse('payments:premium_create_order'))
        self.assertContains(response, reverse('payments:verify'))



class SubscriptionRenewalCtaRenderingTests(TestCase):
    def test_premium_user_far_from_expiry_sees_no_renew_cta(self):
        expiry = timezone.localdate() + timedelta(days=200)
        user, _ = _make_user_with_locker(
            'sub-premium-far@example.com', plan_type='paid', premium_expires_at=expiry,
        )
        self.client.force_login(user)
        response = self.client.get(reverse('accounts:subscription'))
        self.assertNotContains(response, 'Renew Now')

    def test_premium_user_near_expiry_sees_renew_cta(self):
        expiry = timezone.localdate() + timedelta(days=3)
        user, _ = _make_user_with_locker(
            'sub-premium-near@example.com', plan_type='paid', premium_expires_at=expiry,
        )
        self.client.force_login(user)
        response = self.client.get(reverse('accounts:subscription'))
        self.assertContains(response, 'Renew Now')

    def test_premium_user_with_missing_expiry_sees_renew_cta(self):
        user, _ = _make_user_with_locker(
            'sub-premium-null-expiry@example.com', plan_type='paid', premium_expires_at=None,
        )
        self.client.force_login(user)
        response = self.client.get(reverse('accounts:subscription'))
        self.assertContains(response, 'Renew Now')


class PremiumRenewalDueModelTests(TestCase):
    def test_premium_locker_with_missing_expiry_is_renewal_due(self):
        _, locker = _make_user_with_locker(
            'model-null-expiry@example.com', plan_type='paid', premium_expires_at=None,
        )
        self.assertTrue(locker.is_premium)
        self.assertIsNone(locker.premium_expires_at)
        self.assertTrue(locker.premium_renewal_due)
