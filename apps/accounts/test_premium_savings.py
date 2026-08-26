"""Regression tests for apps.accounts.services.calculate_premium_savings
(spec 11a). Covers the real bug: a currently-Free locker with history from
before it downgraded must use each record's *standard* (undiscounted)
amount to compute the hypothetical, never *actual* — actual is already
discounted for that history, so using it double-discounts."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User, Locker
from apps.accounts.services import calculate_premium_savings
from apps.locker.models import Batch
from apps.payments.models import BatchCharge
from apps.personal_shop.models import PersonalShopRequest, PersonalShopQuotation
from apps.shipments.models import Shipment


def _make_locker(email, plan_type='free'):
    user = User.objects.create(email=email, is_active=True)
    return Locker.objects.create(user=user, plan_type=plan_type)


def _make_quotation(locker, standard, actual):
    req = PersonalShopRequest.objects.create(
        locker=locker, request_type='custom_request', status='paid',
    )
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


class PremiumSavingsMixedHistoryTests(TestCase):
    def test_free_locker_with_prior_premium_history_uses_standard_not_actual(self):
        """The core regression: locker is Free NOW but has quotations/
        shipments/batch charges billed at Premium rates from before it
        downgraded (actual < standard already). The hypothetical must be
        computed from `standard`, or it double-discounts the already-
        discounted `actual` figures."""
        locker = _make_locker('mixed-history@example.com', plan_type='free')

        # Priced while Premium: standard 400 -> actual 300 (25% off already applied).
        _make_quotation(locker, standard='400.00', actual='300.00')
        # Priced while Premium: standard 200 -> actual 190 (5% off already applied).
        _make_shipment(locker, standard='200.00', actual='190.00')
        # Priced while Premium: standard 100 -> actual 80 (20% off already applied).
        _make_batch_charge(locker, standard='100.00', actual='80.00')

        result = calculate_premium_savings(locker)

        self.assertFalse(result['is_premium'])
        # Correct: 25% of 400 + 5% of 200 + 20% of 100 = 100 + 10 + 20 = 130.00
        expected = Decimal('400.00') * Decimal('0.25') + Decimal('200.00') * Decimal('0.05') + Decimal('100.00') * Decimal('0.20')
        self.assertEqual(result['amount'], expected.quantize(Decimal('0.01')))
        self.assertEqual(result['amount'], Decimal('130.00'))
        # The bug this guards against: using `actual` instead of `standard`
        # would give 25% of 300 + 5% of 190 + 20% of 80 = 75 + 9.5 + 16 = 100.50.
        self.assertNotEqual(result['amount'], Decimal('100.50'))
        self.assertIn('130.00', result['label'])

    def test_never_premium_free_locker_standard_equals_actual(self):
        """Sanity check: for a locker that was never Premium, standard ==
        actual on every record, so the fix is a no-op here — same result
        either way."""
        locker = _make_locker('always-free@example.com', plan_type='free')
        _make_quotation(locker, standard='400.00', actual='400.00')
        _make_shipment(locker, standard='200.00', actual='200.00')
        _make_batch_charge(locker, standard='100.00', actual='100.00')

        result = calculate_premium_savings(locker)

        self.assertFalse(result['is_premium'])
        self.assertEqual(result['amount'], Decimal('130.00'))

    def test_premium_locker_real_savings_sums_all_three_sources(self):
        locker = _make_locker('real-premium@example.com', plan_type='paid')
        _make_quotation(locker, standard='400.00', actual='300.00')
        _make_shipment(locker, standard='200.00', actual='190.00')
        _make_batch_charge(locker, standard='100.00', actual='80.00')

        result = calculate_premium_savings(locker)

        self.assertTrue(result['is_premium'])
        self.assertEqual(result['amount'], Decimal('130.00'))
        self.assertIn("You've saved", result['label'])

    def test_no_history_returns_zero_and_empty_label(self):
        locker = _make_locker('no-history@example.com', plan_type='free')
        result = calculate_premium_savings(locker)
        self.assertEqual(result['amount'], Decimal('0.00'))
        self.assertEqual(result['label'], '')

    def test_pending_batch_charge_excluded(self):
        """Only status='paid' BatchCharge rows count — a pending one isn't
        real/committed money yet."""
        locker = _make_locker('pending-charge@example.com', plan_type='free')
        batch = Batch.objects.create(
            locker=locker, plan_type_at_creation='free', quota_year=timezone.localdate().year,
            first_parcel_received_date=timezone.localdate(),
        )
        BatchCharge.objects.create(
            batch=batch, charge_date=timezone.localdate(), parcel_count_snapshot=1,
            amount=Decimal('100.00'), amount_standard=Decimal('100.00'), status='pending',
        )
        result = calculate_premium_savings(locker)
        self.assertEqual(result['amount'], Decimal('0.00'))


class LockerRecordPremiumSavingsTests(TestCase):
    """Unit tests for the denormalized counter itself (spec 11a's
    performance fix) — increments happen via an atomic F() UPDATE, not
    live aggregate queries on page load."""

    def test_increments_by_standard_times_rate(self):
        locker = _make_locker('record-basic@example.com', plan_type='free')
        locker.record_premium_savings(Decimal('400.00'), Decimal('0.25'))
        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('100.00'))

    def test_accumulates_across_multiple_calls(self):
        locker = _make_locker('record-accumulate@example.com', plan_type='paid')
        locker.record_premium_savings(Decimal('400.00'), Decimal('0.25'))
        locker.record_premium_savings(Decimal('200.00'), Decimal('0.05'))
        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('110.00'))

    def test_zero_or_none_standard_amount_is_a_noop(self):
        locker = _make_locker('record-zero@example.com', plan_type='free')
        locker.record_premium_savings(Decimal('0.00'), Decimal('0.25'))
        locker.record_premium_savings(None, Decimal('0.25'))
        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('0.00'))

    def test_works_with_bare_pk_only_instance(self):
        """_mark_batch_charges_paid groups multiple lockers' increments via
        Locker(pk=locker_id) without a full SELECT — must still work."""
        locker = _make_locker('record-barepk@example.com', plan_type='free')
        Locker(pk=locker.pk).record_premium_savings(Decimal('100.00'), Decimal('0.20'))
        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('20.00'))


class PremiumSavingsDisplayTests(TestCase):
    def test_zero_amount_hides_banner(self):
        locker = _make_locker('display-zero@example.com', plan_type='free')
        display = locker.premium_savings_display
        self.assertEqual(display['label'], '')
        self.assertEqual(display['amount'], Decimal('0.00'))

    def test_premium_label_wording(self):
        locker = _make_locker('display-premium@example.com', plan_type='paid')
        locker.premium_savings_amount = Decimal('130.00')
        display = locker.premium_savings_display
        self.assertIn("You've saved", display['label'])
        self.assertIn('130.00', display['label'])

    def test_free_label_wording(self):
        locker = _make_locker('display-free@example.com', plan_type='free')
        locker.premium_savings_amount = Decimal('130.00')
        display = locker.premium_savings_display
        self.assertIn('You could have saved', display['label'])
        self.assertIn('upgrade now', display['label'])


class PremiumSavingsIncrementalUpdateTests(TestCase):
    """Covers the three exact points premium_savings_amount is incremented
    at finalize-as-paid time — no per-request aggregate queries involved."""

    def test_quotation_mark_paid_increments_for_purchase_type(self):
        locker = _make_locker('finalize-quote@example.com', plan_type='free')
        req = PersonalShopRequest.objects.create(locker=locker, request_type='custom_request', status='quotation_ready')
        quotation = PersonalShopQuotation.objects.create(
            request=req, quotation_type='purchase', status='pending',
            service_fee_standard_amount=Decimal('400.00'), service_fee_amount=Decimal('400.00'),
            total_amount=Decimal('400.00'), valid_until=timezone.now() + timedelta(hours=48),
        )
        req.active_quotation = quotation
        req.save()

        req.mark_paid()
        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('100.00'))

    def test_quotation_mark_paid_skips_non_purchase_type(self):
        locker = _make_locker('finalize-research@example.com', plan_type='free')
        req = PersonalShopRequest.objects.create(locker=locker, request_type='custom_request', status='quotation_ready')
        quotation = PersonalShopQuotation.objects.create(
            request=req, quotation_type='research_fee', status='pending',
            research_fee_amount=Decimal('400.00'), total_amount=Decimal('400.00'),
            valid_until=timezone.now() + timedelta(hours=48),
        )
        req.active_quotation = quotation
        req.save()

        req.mark_paid()
        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('0.00'))

    def test_shipment_paid_finalize_increments(self):
        from apps.payments.views import _record_shipment_premium_savings

        locker = _make_locker('finalize-shipment@example.com', plan_type='free')
        shipment = _make_shipment(locker, standard='200.00', actual='190.00')

        _record_shipment_premium_savings(shipment)
        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('10.00'))

    def test_batch_charges_paid_finalize_increments_grouped_by_locker(self):
        from apps.payments.models import Payment
        from apps.payments.views import _mark_batch_charges_paid
        import json

        locker_a = _make_locker('finalize-batch-a@example.com', plan_type='free')
        locker_b = _make_locker('finalize-batch-b@example.com', plan_type='paid')

        batch_a = Batch.objects.create(
            locker=locker_a, plan_type_at_creation='free', quota_year=timezone.localdate().year,
            first_parcel_received_date=timezone.localdate(),
        )
        batch_b = Batch.objects.create(
            locker=locker_b, plan_type_at_creation='paid', quota_year=timezone.localdate().year,
            first_parcel_received_date=timezone.localdate(),
        )
        charge_a = BatchCharge.objects.create(
            batch=batch_a, charge_date=timezone.localdate(), parcel_count_snapshot=1,
            amount=Decimal('100.00'), amount_standard=Decimal('100.00'), status='pending',
        )
        charge_b = BatchCharge.objects.create(
            batch=batch_b, charge_date=timezone.localdate(), parcel_count_snapshot=1,
            amount=Decimal('80.00'), amount_standard=Decimal('100.00'), status='pending',
        )
        payment = Payment.objects.create(
            user=locker_a.user, amount=Decimal('180.00'), payment_type='storage_batch',
            payment_method='razorpay', status='captured',
            notes=json.dumps({'batch_charge_ids': [str(charge_a.pk), str(charge_b.pk)]}),
        )

        _mark_batch_charges_paid(payment)

        charge_a.refresh_from_db()
        charge_b.refresh_from_db()
        locker_a.refresh_from_db()
        locker_b.refresh_from_db()

        self.assertEqual(charge_a.status, 'paid')
        self.assertEqual(charge_b.status, 'paid')
        # 20% of each charge's standard amount (100.00) = 20.00, credited to
        # its own locker — not pooled across the two lockers in this payment.
        self.assertEqual(locker_a.premium_savings_amount, Decimal('20.00'))
        self.assertEqual(locker_b.premium_savings_amount, Decimal('20.00'))
