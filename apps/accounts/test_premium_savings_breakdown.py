"""Tests for apps.accounts.services.calculate_premium_savings_breakdown —
the per-category version of calculate_premium_savings() backing the
Account > Subscription page. Same standard-not-actual rules as
test_premium_savings.py, plus consolidation (100%-off for Premium, not a
percentage rate) and the reconciliation check against
Locker.premium_savings_amount."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User, Locker
from apps.accounts.services import (
    build_sparkline_geometry,
    calculate_premium_savings_breakdown,
    calculate_premium_savings_trend,
    windowed_savings_pct,
)
from apps.locker.models import Batch
from apps.payments.models import BatchCharge
from apps.personal_shop.models import PersonalShopRequest, PersonalShopQuotation
from apps.shipments.models import Shipment


def _month_start(months_ago, base=None):
    """Deterministic first-of-month timestamp N calendar months before
    `base` (default now) — avoids the flakiness of `timedelta(days=30*n)`
    landing in the wrong month depending on what day the test happens to
    run. Used wherever a test needs guaranteed-distinct calendar months."""
    base = base or timezone.now()
    year, month = base.year, base.month - months_ago
    while month <= 0:
        month += 12
        year -= 1
    return base.replace(year=year, month=month, day=1, hour=12, minute=0, second=0, microsecond=0)


def _make_quotation_paid_months_ago(locker, standard, actual, months_ago):
    req = PersonalShopRequest.objects.create(locker=locker, request_type='custom_request', status='paid')
    PersonalShopRequest.objects.filter(pk=req.pk).update(paid_at=_month_start(months_ago))
    return PersonalShopQuotation.objects.create(
        request=req, quotation_type='purchase', status='approved',
        service_fee_standard_amount=Decimal(standard), service_fee_amount=Decimal(actual),
        total_amount=Decimal(actual), valid_until=timezone.now() + timedelta(hours=48),
    )


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


def _make_shipment(locker, standard, actual, consolidation_standard='0.00', consolidation_actual='0.00'):
    return Shipment.objects.create(
        user=locker.user, shipment_type='international', payment_status='paid',
        recipient_name='Test', recipient_phone='0000000000',
        address_line1='Addr', city='City', state='State', postal_code='000000',
        shipping_cost=Decimal(actual), shipping_cost_standard=Decimal(standard),
        consolidation_fee=Decimal(consolidation_actual), consolidation_fee_standard=Decimal(consolidation_standard),
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


class PremiumSavingsBreakdownTests(TestCase):
    def test_no_history_returns_zero_categories(self):
        """Brand-new user, zero history — every category ₹0, no error."""
        locker = _make_locker('breakdown-empty@example.com', plan_type='free')
        result = calculate_premium_savings_breakdown(locker)
        self.assertEqual(result['total'], Decimal('0.00'))
        for amount in result['categories'].values():
            self.assertEqual(amount, Decimal('0.00'))

    def test_premium_locker_itemizes_real_savings_per_category(self):
        locker = _make_locker('breakdown-premium@example.com', plan_type='paid')
        _make_quotation(locker, standard='400.00', actual='300.00')
        _make_shipment(locker, standard='200.00', actual='190.00',
                        consolidation_standard='50.00', consolidation_actual='0.00')
        _make_batch_charge(locker, standard='100.00', actual='80.00')

        result = calculate_premium_savings_breakdown(locker)

        self.assertTrue(result['is_premium'])
        self.assertEqual(result['categories']['trunkassist'], Decimal('100.00'))
        self.assertEqual(result['categories']['shipping'], Decimal('10.00'))
        self.assertEqual(result['categories']['storage'], Decimal('20.00'))
        self.assertEqual(result['categories']['consolidation'], Decimal('50.00'))
        self.assertEqual(result['total'], Decimal('180.00'))

    def test_free_locker_itemizes_hypothetical_from_standard_not_actual(self):
        """The standard-not-actual regression, per category: history from
        before a downgrade already has actual < standard, so the
        hypothetical must be built from `standard`."""
        locker = _make_locker('breakdown-free@example.com', plan_type='free')
        _make_quotation(locker, standard='400.00', actual='300.00')
        _make_shipment(locker, standard='200.00', actual='190.00',
                        consolidation_standard='50.00', consolidation_actual='50.00')
        _make_batch_charge(locker, standard='100.00', actual='80.00')

        result = calculate_premium_savings_breakdown(locker)

        self.assertFalse(result['is_premium'])
        self.assertEqual(result['categories']['trunkassist'], Decimal('100.00'))  # 25% of 400
        self.assertEqual(result['categories']['shipping'], Decimal('10.00'))       # 5% of 200
        self.assertEqual(result['categories']['storage'], Decimal('20.00'))        # 20% of 100
        # Consolidation is 100%-off for Premium, not a rate: full standard amount.
        self.assertEqual(result['categories']['consolidation'], Decimal('50.00'))
        self.assertEqual(result['total'], Decimal('180.00'))

    def test_reconciles_against_denormalized_premium_savings_amount(self):
        """The live breakdown's total (trunkassist+shipping+storage+
        consolidation) must match Locker.premium_savings_amount for the
        same history — two independently-built code paths agreeing, now
        that consolidation has its own record_premium_savings() call site
        (apps/payments/views.py::_record_shipment_premium_savings)."""
        locker = _make_locker('breakdown-reconcile@example.com', plan_type='paid')
        _make_quotation(locker, standard='400.00', actual='300.00')
        _make_shipment(locker, standard='200.00', actual='190.00',
                        consolidation_standard='50.00', consolidation_actual='0.00')
        _make_batch_charge(locker, standard='100.00', actual='80.00')
        locker.record_premium_savings(Decimal('400.00'), Locker.PREMIUM_SERVICE_FEE_DISCOUNT_RATE)
        locker.record_premium_savings(Decimal('200.00'), Locker.PREMIUM_SHIPPING_DISCOUNT_RATE)
        locker.record_premium_savings(Decimal('100.00'), Locker.PREMIUM_STORAGE_DISCOUNT_RATE)
        locker.record_premium_savings(Decimal('50.00'), Decimal('1.00'))  # consolidation: 100% off
        locker.refresh_from_db()

        result = calculate_premium_savings_breakdown(locker)

        self.assertEqual(result['total'], locker.premium_savings_amount)

    def test_reconciles_across_a_free_to_premium_upgrade(self):
        """A locker that paid one quotation while Free (no real discount —
        actual == standard), then upgraded to Premium and paid a second
        quotation with a real discount, must still reconcile against
        Locker.premium_savings_amount. Locker-level is_premium branching
        (the bug this test guards against) would price the Free-era row
        using the locker's now-current 'paid' state — discount(400, 400) = 0
        instead of the hypothetical 400 * 0.25 = 100 — silently losing that
        row's contribution and diverging from the denormalized counter,
        which always credited it as a hypothetical at the time it was paid."""
        locker = _make_locker('breakdown-upgrade@example.com', plan_type='free')
        _make_quotation(locker, standard='400.00', actual='400.00')  # paid while Free: no real discount
        locker.record_premium_savings(Decimal('400.00'), Locker.PREMIUM_SERVICE_FEE_DISCOUNT_RATE)

        locker.plan_type = 'paid'
        locker.save(update_fields=['plan_type'])
        _make_quotation(locker, standard='400.00', actual='300.00')  # paid while Premium: real 25% discount
        locker.record_premium_savings(Decimal('400.00'), Locker.PREMIUM_SERVICE_FEE_DISCOUNT_RATE)
        locker.refresh_from_db()

        result = calculate_premium_savings_breakdown(locker)

        self.assertEqual(result['categories']['trunkassist'], Decimal('200.00'))  # 100 hypothetical + 100 real
        self.assertEqual(result['total'], locker.premium_savings_amount)

    def test_pending_batch_charge_excluded(self):
        locker = _make_locker('breakdown-pending@example.com', plan_type='free')
        batch = Batch.objects.create(
            locker=locker, plan_type_at_creation='free', quota_year=timezone.localdate().year,
            first_parcel_received_date=timezone.localdate(),
        )
        BatchCharge.objects.create(
            batch=batch, charge_date=timezone.localdate(), parcel_count_snapshot=1,
            amount=Decimal('100.00'), amount_standard=Decimal('100.00'), status='pending',
        )
        result = calculate_premium_savings_breakdown(locker)
        self.assertEqual(result['categories']['storage'], Decimal('0.00'))


class PremiumSavingsCategoryDetailTests(TestCase):
    """Tests for the Subscription page's richer per-category detail
    (standard/effective/pct/count), added alongside category_detail for
    spec: itemized Without Premium / With Premium rows and % pills."""

    def test_zero_standard_guards_against_division_by_zero(self):
        """A brand-new locker has standard == 0 for every category — pct
        must come back None (rendered as '—'), never raise."""
        locker = _make_locker('detail-empty@example.com', plan_type='free')
        result = calculate_premium_savings_breakdown(locker)
        for detail in result['category_detail'].values():
            self.assertIsNone(detail['pct'])
            self.assertEqual(detail['count'], 0)
            self.assertEqual(detail['effective'], Decimal('0.00'))

    def test_premium_category_detail_standard_effective_and_pct(self):
        locker = _make_locker('detail-premium@example.com', plan_type='paid')
        _make_quotation(locker, standard='400.00', actual='300.00')
        _make_shipment(locker, standard='200.00', actual='190.00',
                        consolidation_standard='50.00', consolidation_actual='0.00')
        _make_batch_charge(locker, standard='100.00', actual='80.00')

        result = calculate_premium_savings_breakdown(locker)
        trunkassist = result['category_detail']['trunkassist']
        self.assertEqual(trunkassist['standard'], Decimal('400.00'))
        self.assertEqual(trunkassist['effective'], Decimal('300.00'))
        self.assertEqual(trunkassist['pct'], Decimal('25.0'))
        self.assertEqual(trunkassist['count'], 1)

        consolidation = result['category_detail']['consolidation']
        self.assertEqual(consolidation['standard'], Decimal('50.00'))
        self.assertEqual(consolidation['effective'], Decimal('0.00'))
        self.assertEqual(consolidation['pct'], Decimal('100.0'))

    def test_categories_used_counts_only_categories_with_activity(self):
        locker = _make_locker('detail-used@example.com', plan_type='paid')
        _make_quotation(locker, standard='400.00', actual='300.00')
        # No shipment, no batch charge, no consolidation.
        result = calculate_premium_savings_breakdown(locker)
        self.assertEqual(result['categories_used'], 1)
        self.assertEqual(result['shipments_count'], 0)

    def test_shipments_count_reflects_paid_shipments(self):
        locker = _make_locker('detail-shipcount@example.com', plan_type='paid')
        _make_shipment(locker, standard='200.00', actual='190.00')
        _make_shipment(locker, standard='300.00', actual='285.00')
        result = calculate_premium_savings_breakdown(locker)
        self.assertEqual(result['shipments_count'], 2)


class WindowedSavingsPctTests(TestCase):
    """windowed_savings_pct() backs the hero sparkline's percentage badge.
    It is deliberately scoped to the SAME up-to-6-month window
    calculate_premium_savings_trend() plots — see
    test_badge_and_chart_cover_the_same_months below for the invariant
    that actually matters: the two must never describe different periods."""

    def test_none_when_no_history(self):
        locker = _make_locker('pct-empty@example.com', plan_type='free')
        self.assertIsNone(windowed_savings_pct(locker))

    def test_none_below_two_months(self):
        """A single month of activity has no window to compute a
        percentage over — same 'nothing to plot' gate as the chart."""
        locker = _make_locker('pct-onemonth@example.com', plan_type='paid')
        _make_quotation_paid_months_ago(locker, '400.00', '300.00', months_ago=0)
        self.assertIsNone(windowed_savings_pct(locker))

    def test_blended_percentage_across_two_months(self):
        locker = _make_locker('pct-blended@example.com', plan_type='paid')
        _make_quotation_paid_months_ago(locker, '400.00', '300.00', months_ago=1)  # 100 off 400
        _make_quotation_paid_months_ago(locker, '200.00', '190.00', months_ago=0)  # 10 off 200
        # total discount 110 / total standard 600 = 18.33% -> rounds to 18
        self.assertEqual(windowed_savings_pct(locker), 18)

    def test_stays_bounded_where_a_naive_growth_rate_would_spike(self):
        """Regression for the metric this function replaced: a
        month-over-month 'growth since first month' rate
        ((last - first) / first * 100) blows up to an absurd,
        screenshot-worthy number when the first month's activity is tiny
        relative to a later month — exactly the real-world shape of a new
        Premium subscriber's history. Confirm (a) that naive formula
        really would have spiked here, and (b) windowed_savings_pct(),
        which never looks at a first/last split, stays within a sane
        0-100% bound on the same underlying data."""
        locker = _make_locker('pct-lopsided@example.com', plan_type='paid')
        _make_quotation_paid_months_ago(locker, '10.00', '8.00', months_ago=1)      # tiny: 2 off 10
        _make_quotation_paid_months_ago(locker, '2000.00', '1500.00', months_ago=0)  # large: 500 off 2000

        first_month_discount = Decimal('2.00')
        total_discount = Decimal('502.00')
        naive_growth_pct = (total_discount - first_month_discount) / first_month_discount * 100
        self.assertGreater(naive_growth_pct, 1000)  # confirms the old-style formula really would misfire

        pct = windowed_savings_pct(locker)
        self.assertIsNotNone(pct)
        self.assertGreaterEqual(pct, 0)
        self.assertLessEqual(pct, 100)

    def test_badge_and_chart_cover_the_same_months(self):
        """The invariant that actually matters: a locker with MORE than 6
        months of history must not have the badge pull in months the
        chart doesn't plot. Heavy usage 8 months ago (outside the 6-month
        window) would inflate a lifetime percentage to 60%; the last 6
        months alone (light, steady usage) only justify 10%. The badge
        must report 10%, matching exactly what the chart shows — not the
        60% a lifetime calculation would produce."""
        locker = _make_locker('pct-windowed@example.com', plan_type='paid')
        _make_quotation_paid_months_ago(locker, '2000.00', '500.00', months_ago=8)  # 1500 off 2000, OUTSIDE window
        for months_ago in range(5, -1, -1):  # months 5..0 -> 6 months, INSIDE window
            _make_quotation_paid_months_ago(locker, '100.00', '90.00', months_ago=months_ago)  # 10 off 100 each

        trend = calculate_premium_savings_trend(locker)
        pct = windowed_savings_pct(locker)

        self.assertEqual(len(trend), 6)  # exactly the 6 in-window months, month 8 excluded

        # Lifetime figure (what the old, replaced implementation returned):
        # (1500 + 6*10) / (2000 + 6*100) * 100 = 1560/2600*100 = 60%.
        lifetime_pct = 60
        self.assertNotEqual(pct, lifetime_pct)
        # Windowed figure: 6*10 / 6*100 * 100 = 10%, matching the chart's 6 months exactly.
        self.assertEqual(pct, 10)


class PremiumSavingsTrendTests(TestCase):
    def test_free_locker_has_no_trend(self):
        """A Free locker's numbers are hypothetical — no real payment
        timeline exists to plot."""
        locker = _make_locker('trend-free@example.com', plan_type='free')
        self.assertEqual(calculate_premium_savings_trend(locker), [])

    def test_single_month_of_activity_returns_empty(self):
        """Below 2 data points there's nothing to draw a line through."""
        locker = _make_locker('trend-single@example.com', plan_type='paid')
        _make_quotation(locker, standard='400.00', actual='300.00')
        self.assertEqual(calculate_premium_savings_trend(locker), [])

    def test_cumulative_across_months(self):
        locker = _make_locker('trend-multi@example.com', plan_type='paid')
        now = timezone.now()
        req1 = PersonalShopRequest.objects.create(locker=locker, request_type='custom_request', status='paid')
        PersonalShopRequest.objects.filter(pk=req1.pk).update(paid_at=now - timedelta(days=90))
        PersonalShopQuotation.objects.create(
            request=req1, quotation_type='purchase', status='approved',
            service_fee_standard_amount=Decimal('400.00'), service_fee_amount=Decimal('300.00'),
            total_amount=Decimal('300.00'), valid_until=now + timedelta(hours=48),
        )
        req2 = PersonalShopRequest.objects.create(locker=locker, request_type='custom_request', status='paid')
        PersonalShopRequest.objects.filter(pk=req2.pk).update(paid_at=now - timedelta(days=1))
        PersonalShopQuotation.objects.create(
            request=req2, quotation_type='purchase', status='approved',
            service_fee_standard_amount=Decimal('400.00'), service_fee_amount=Decimal('300.00'),
            total_amount=Decimal('300.00'), valid_until=now + timedelta(hours=48),
        )

        trend = calculate_premium_savings_trend(locker)
        self.assertEqual(len(trend), 2)
        self.assertEqual(trend[0]['value'], Decimal('100.00'))
        self.assertEqual(trend[1]['value'], Decimal('200.00'))  # cumulative


class SparklineGeometryTests(TestCase):
    def test_none_below_two_points(self):
        self.assertIsNone(build_sparkline_geometry([]))
        self.assertIsNone(build_sparkline_geometry([{'label': 'Jan', 'value': Decimal('10.00')}]))

    def test_two_points_span_full_width(self):
        trend = [
            {'label': 'Jan', 'value': Decimal('10.00')},
            {'label': 'Feb', 'value': Decimal('20.00')},
        ]
        geometry = build_sparkline_geometry(trend, width=240, height=70)
        self.assertEqual(len(geometry['dots']), 2)
        self.assertEqual(geometry['dots'][0]['x'], 0)
        self.assertEqual(geometry['dots'][1]['x'], 240)
