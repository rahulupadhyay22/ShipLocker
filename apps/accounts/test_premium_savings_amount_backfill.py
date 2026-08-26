"""Regression tests for migration 0007 (backfill_premium_savings_amount).

Bug: the original backfill credited `standard_amount * rate` for every
locker regardless of plan_type, so a Premium locker with legacy history
(where personal_shop 0007 / shipments 0008 backfilled standard == actual,
i.e. zero real discount ever applied) got a nonzero premium_savings_amount
anyway. Fixed version branches on plan_type: Premium lockers get the real
discount (standard - actual, matching calculate_premium_savings_breakdown);
Free lockers keep the unchanged hypothetical (standard * rate)."""
import importlib
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from datetime import timedelta

from apps.accounts.models import User, Locker
from apps.locker.models import Batch
from apps.payments.models import BatchCharge
from apps.personal_shop.models import PersonalShopRequest, PersonalShopQuotation
from apps.shipments.models import Shipment

migration_module = importlib.import_module(
    'apps.accounts.migrations.0007_backfill_premium_savings_amount'
)


def _make_locker(email, plan_type='free'):
    user = User.objects.create(email=email, is_active=True)
    return Locker.objects.create(user=user, plan_type=plan_type)


def _make_quotation(locker, standard, actual):
    req = PersonalShopRequest.objects.create(locker=locker, request_type='custom_request', status='paid')
    return PersonalShopQuotation.objects.create(
        request=req, quotation_type='purchase', status='approved',
        service_fee_standard_amount=Decimal(standard), service_fee_amount=Decimal(actual),
        total_amount=Decimal(actual), valid_until=timezone.now() + timedelta(hours=48),
    )


def _make_shipment(locker, standard, actual):
    return Shipment.objects.create(
        user=locker.user, shipment_type='international', payment_status='paid',
        recipient_name='Test', recipient_phone='0000000000',
        address_line1='Addr', city='City', state='State', postal_code='000000',
        shipping_cost=Decimal(actual), shipping_cost_standard=Decimal(standard),
    )


def _make_batch_charge(locker, standard, actual):
    batch = Batch.objects.create(
        locker=locker, plan_type_at_creation=locker.plan_type, quota_year=timezone.localdate().year,
        first_parcel_received_date=timezone.localdate(),
    )
    return BatchCharge.objects.create(
        batch=batch, charge_date=timezone.localdate(), parcel_count_snapshot=1,
        amount=Decimal(actual), amount_standard=Decimal(standard), status='paid',
    )


def _run_backfill():
    from django.apps import apps as django_apps
    migration_module.backfill_premium_savings_amount(django_apps, None)


class PremiumSavingsBackfillTests(TestCase):
    def test_premium_locker_savings_is_standard_minus_actual(self):
        """Test 1: real discount, not a flat rate applied to standard."""
        locker = _make_locker('backfill-premium@example.com', plan_type='paid')
        _make_quotation(locker, standard='400.00', actual='300.00')  # 100 real discount
        _make_shipment(locker, standard='200.00', actual='190.00')   # 10 real discount
        _make_batch_charge(locker, standard='100.00', actual='80.00')  # 20 real discount

        _run_backfill()
        locker.refresh_from_db()

        self.assertEqual(locker.premium_savings_amount, Decimal('130.00'))

    def test_premium_locker_with_no_actual_discount_backfills_zero(self):
        """Test 2: legacy row where standard was backfilled equal to actual
        (no discount was ever really applied) must not manufacture savings."""
        locker = _make_locker('backfill-premium-nodiscount@example.com', plan_type='paid')
        _make_quotation(locker, standard='400.00', actual='400.00')
        _make_shipment(locker, standard='200.00', actual='200.00')
        _make_batch_charge(locker, standard='100.00', actual='100.00')

        _run_backfill()
        locker.refresh_from_db()

        self.assertEqual(locker.premium_savings_amount, Decimal('0.00'))

    def test_free_locker_savings_still_hypothetical_from_standard(self):
        """Test 3: Free-locker behavior is unchanged by the fix — no actual
        discount exists to subtract, so it stays standard * rate."""
        locker = _make_locker('backfill-free@example.com', plan_type='free')
        _make_quotation(locker, standard='400.00', actual='400.00')   # 25% of 400 = 100
        _make_shipment(locker, standard='200.00', actual='200.00')    # 5% of 200 = 10
        _make_batch_charge(locker, standard='100.00', actual='100.00')  # 20% of 100 = 20

        _run_backfill()
        locker.refresh_from_db()

        self.assertEqual(locker.premium_savings_amount, Decimal('130.00'))
