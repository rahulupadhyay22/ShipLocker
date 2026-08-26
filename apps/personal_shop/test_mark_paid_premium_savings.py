"""Spec 11a — integration tests for call site #1: PersonalShopRequest.mark_paid()
incrementing Locker.premium_savings_amount.

Scope: does calling mark_paid() actually produce the correct
premium_savings_amount increment on the right locker, for Premium and Free
lockers, and correctly skip non-'purchase' quotation types. Does NOT
re-test record_premium_savings()'s own atomicity/rounding/no-op behavior —
that's apps/accounts/test_premium_savings.py's job.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Locker, User
from .models import PersonalShopRequest, PersonalShopQuotation


def _make_locker(email, plan_type='free'):
    user = User.objects.create(email=email, is_active=True)
    return Locker.objects.create(user=user, plan_type=plan_type)


def _make_request(locker, request_type='custom_request', status='quotation_ready'):
    return PersonalShopRequest.objects.create(
        locker=locker, request_type=request_type, status=status,
    )


def _make_quotation(request, quotation_type='purchase', service_fee_standard_amount=Decimal('1000.00'), status='pending'):
    quotation = PersonalShopQuotation.objects.create(
        request=request, quotation_type=quotation_type,
        service_fee_standard_amount=service_fee_standard_amount,
        total_amount=Decimal('1000.00'),
        valid_until=timezone.now() + timedelta(hours=48),
        status=status,
    )
    request.active_quotation = quotation
    request.save()
    return quotation


class MarkPaidPurchaseQuotationPremiumSavingsTests(TestCase):
    """Purchase-type quotations are the only ones with a service fee to
    discount — mark_paid() should increment premium_savings_amount by
    service_fee_standard_amount * PREMIUM_SERVICE_FEE_DISCOUNT_RATE."""

    def test_premium_locker_increments_by_standard_times_rate(self):
        locker = _make_locker('purchase-premium@example.com', plan_type='paid')
        request = _make_request(locker)
        _make_quotation(request, service_fee_standard_amount=Decimal('1000.00'))

        request.mark_paid()

        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('250.00'))

    def test_free_locker_still_increments_same_formula(self):
        # Spec: always standard_amount * rate regardless of current plan_type —
        # a hypothetical for Free users, real savings for Premium, same number.
        locker = _make_locker('purchase-free@example.com', plan_type='free')
        request = _make_request(locker)
        _make_quotation(request, service_fee_standard_amount=Decimal('1000.00'))

        request.mark_paid()

        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('250.00'))

    def test_zero_service_fee_standard_amount_does_not_increment(self):
        locker = _make_locker('purchase-zero-fee@example.com', plan_type='paid')
        request = _make_request(locker)
        _make_quotation(request, service_fee_standard_amount=Decimal('0.00'))

        request.mark_paid()

        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('0.00'))

    def test_only_the_owning_locker_is_incremented(self):
        locker_a = _make_locker('purchase-owner-a@example.com', plan_type='paid')
        locker_b = _make_locker('purchase-owner-b@example.com', plan_type='paid')
        request = _make_request(locker_a)
        _make_quotation(request, service_fee_standard_amount=Decimal('1000.00'))

        request.mark_paid()

        locker_a.refresh_from_db()
        locker_b.refresh_from_db()
        self.assertEqual(locker_a.premium_savings_amount, Decimal('250.00'))
        self.assertEqual(locker_b.premium_savings_amount, Decimal('0.00'))


class MarkPaidNonPurchaseQuotationTests(TestCase):
    """research_fee/expense_advance quotations have no service_fee_standard_amount
    to discount — mark_paid() must NOT call record_premium_savings for them."""

    def test_research_fee_quotation_does_not_increment(self):
        locker = _make_locker('research-fee@example.com', plan_type='paid')
        request = _make_request(locker, request_type='custom_request')
        _make_quotation(
            request, quotation_type='research_fee',
            service_fee_standard_amount=Decimal('1000.00'),
        )

        request.mark_paid()

        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('0.00'))

    def test_expense_advance_quotation_does_not_increment(self):
        locker = _make_locker('expense-advance@example.com', plan_type='paid')
        request = _make_request(locker, request_type='boutique_purchase')
        _make_quotation(
            request, quotation_type='expense_advance',
            service_fee_standard_amount=Decimal('1000.00'),
        )

        request.mark_paid()

        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('0.00'))


class MarkPaidNoOpPathsTests(TestCase):
    """Paths where mark_paid() should never touch premium_savings_amount at all."""

    def test_cancelled_request_is_noop(self):
        locker = _make_locker('cancelled-request@example.com', plan_type='paid')
        request = _make_request(locker, status='cancelled')
        _make_quotation(request, service_fee_standard_amount=Decimal('1000.00'))

        result = request.mark_paid()

        self.assertFalse(result)
        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('0.00'))

    def test_no_active_quotation_is_noop_for_savings(self):
        locker = _make_locker('no-quotation@example.com', plan_type='paid')
        request = _make_request(locker)
        # No active_quotation assigned at all.

        request.mark_paid()

        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('0.00'))


class MarkPaidIdempotencyTests(TestCase):
    """A duplicate finalize call (e.g. retried webhook/verify request calling
    mark_paid() twice) must not double-increment. Re-fetches a fresh row for
    the second call so the guard is exercised against real DB state, not a
    stale in-memory quotation.status."""

    def test_second_mark_paid_call_does_not_double_increment(self):
        locker = _make_locker('idempotent-purchase@example.com', plan_type='paid')
        request = _make_request(locker)
        _make_quotation(request, service_fee_standard_amount=Decimal('1000.00'))

        request.mark_paid()
        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('250.00'))

        # Simulate a duplicate finalize call arriving later — fetch a fresh
        # instance so active_quotation.status is read from the DB, not a
        # cached attribute on the original `request` object.
        fresh_request = PersonalShopRequest.objects.get(pk=request.pk)
        fresh_request.mark_paid()

        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('250.00'))
