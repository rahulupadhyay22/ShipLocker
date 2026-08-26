"""Regression tests for migration 0007 (backfill_service_fee_standard_amount).

Bug: 0006 added service_fee_standard_amount with default=0 and no backfill.
Every pre-existing pending/purchase PersonalShopQuotation row got
service_fee_standard_amount=0 while its real service_fee_amount could be
nonzero. refresh_service_fee_discount() -- called with no admin action
required from PersonalShopQuotationView.get / CreatePersonalShopPaymentOrderView.post
-- would then "correct" service_fee_amount down to 0, silently zeroing the
charge. 0007 backfills service_fee_standard_amount = service_fee_amount for
any row still at the migration default (0) with a genuinely nonzero fee.
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from datetime import timedelta

from apps.accounts.models import User, Locker
from apps.personal_shop.models import PersonalShopRequest, PersonalShopQuotation


def _make_locker(email, plan_type='free'):
    user = User.objects.create(email=email, is_active=True)
    return Locker.objects.create(user=user, plan_type=plan_type)


def _make_request(locker, status='quotation_ready', **extra):
    return PersonalShopRequest.objects.create(
        locker=locker, request_type='custom_request', status=status, **extra,
    )


def _make_quotation(req, **extra):
    defaults = dict(
        status='pending', quotation_type='purchase',
        valid_until=timezone.now() + timedelta(hours=48),
        total_amount=Decimal('500.00'),
    )
    defaults.update(extra)
    return PersonalShopQuotation.objects.create(request=req, **defaults)


class MigrationBackfillFunctionTests(TestCase):
    """Exercises the RunPython backfill function directly against real
    PersonalShopQuotation rows, matching the pattern used by
    apps/shipments/tests/test_shipping_discount.py::MigrationBackfillTests
    for the sibling shipping/consolidation-fee backfill (migration 0008)."""

    def test_backfill_sets_standard_only_for_zero_standard_nonzero_fee_rows(self):
        import importlib
        migration_module = importlib.import_module(
            'apps.personal_shop.migrations.0007_backfill_service_fee_standard_amount'
        )

        locker = _make_locker('backfill@example.com')
        req = _make_request(locker)

        # Pre-existing row: real fee was charged, standard field defaulted to 0.
        stale = _make_quotation(req, service_fee_amount=Decimal('500.00'))
        stale.service_fee_standard_amount = Decimal('0')
        stale.save(update_fields=['service_fee_standard_amount'])

        # A row that legitimately has both fields at 0 (e.g. non-purchase
        # quotation type where the fee is unused) must NOT be touched.
        untouched = _make_quotation(
            _make_request(locker), quotation_type='research_fee',
            service_fee_amount=Decimal('0'),
        )
        untouched.service_fee_standard_amount = Decimal('0')
        untouched.save(update_fields=['service_fee_standard_amount'])

        from django.apps import apps as django_apps
        migration_module.backfill_service_fee_standard_amount(django_apps, None)

        stale.refresh_from_db()
        untouched.refresh_from_db()

        self.assertEqual(stale.service_fee_standard_amount, Decimal('500.00'))
        self.assertEqual(untouched.service_fee_standard_amount, Decimal('0'))


class RefreshServiceFeeDiscountBackfillRegressionTests(TestCase):
    """Proves the actual bug is fixed: a quotation in the exact pre-0007
    shape (service_fee_standard_amount=0, service_fee_amount nonzero,
    status='pending', quotation_type='purchase') must not have its
    service_fee_amount silently zeroed by refresh_service_fee_discount()
    once service_fee_standard_amount correctly reflects the real fee (i.e.
    after the backfill has run, as it now does for every existing row)."""

    def test_refresh_does_not_zero_fee_once_standard_amount_is_backfilled(self):
        locker = _make_locker('refresh-fix@example.com', plan_type='free')
        req = _make_request(locker)
        quotation = _make_quotation(req, service_fee_amount=Decimal('500.00'))

        # Simulate the pre-migration-0007 broken state directly (bypassing
        # whatever normally sets it), then apply the backfill exactly like
        # the migration does.
        quotation.service_fee_standard_amount = Decimal('0')
        quotation.save(update_fields=['service_fee_standard_amount'])

        import importlib
        migration_module = importlib.import_module(
            'apps.personal_shop.migrations.0007_backfill_service_fee_standard_amount'
        )
        from django.apps import apps as django_apps
        migration_module.backfill_service_fee_standard_amount(django_apps, None)

        quotation.refresh_from_db()
        self.assertEqual(quotation.service_fee_standard_amount, Decimal('500.00'))

        quotation.refresh_service_fee_discount()
        quotation.refresh_from_db()

        # Bug would have zeroed this to 0 (free locker, discount off of a
        # bogus standard=0). After the backfill it stays the real charge.
        self.assertEqual(quotation.service_fee_amount, Decimal('500.00'))

    def test_zero_standard_amount_still_zeroes_fee_documents_why_backfill_is_required(self):
        """Characterizes refresh_service_fee_discount()'s existing (unchanged)
        behavior: given a still-broken row (service_fee_standard_amount=0,
        e.g. one the 0007 backfill was never run against), it DOES zero the
        fee. This is not new/desired behavior -- it is the reason the
        migration backfill is necessary rather than optional, and documents
        the bug this task fixes at the data layer, not the model layer (an
        actual guard in refresh_service_fee_discount was explicitly out of
        scope for this task). If this test ever goes red because
        refresh_service_fee_discount grew such a guard, that's a deliberate
        improvement -- update/remove this test rather than treating it as a
        regression."""
        locker = _make_locker('refresh-bug@example.com', plan_type='free')
        req = _make_request(locker)
        quotation = _make_quotation(req, service_fee_amount=Decimal('500.00'))
        quotation.service_fee_standard_amount = Decimal('0')
        quotation.save(update_fields=['service_fee_standard_amount'])

        quotation.refresh_service_fee_discount()
        quotation.refresh_from_db()

        self.assertEqual(quotation.service_fee_amount, Decimal('0.00'))
