"""Spec 11a — integration tests for call sites #2 and #3:

  #2 _record_shipment_premium_savings(shipment) (apps/payments/views.py),
     called from both VerifyPaymentView.post and RazorpayWebhookView.post
     right after shipment.payment_status flips to 'paid'.
  #3 _mark_batch_charges_paid(payment) (apps/payments/views.py) — bulk
     .update() of BatchCharge rows, snapshotted per-locker before the flip.

Scope: does completing a shipment/batch-charge payment actually produce the
correct premium_savings_amount increment on the right locker(s), for Premium
and Free lockers, without cross-contaminating lockers in the grouped
batch-charge path, and without double-incrementing on a retried/duplicate
capture. Does NOT re-test record_premium_savings()'s own atomicity/rounding —
that's apps/accounts/test_premium_savings.py's job.

Note on scope: _record_shipment_premium_savings records two components —
shipping (shipping_cost_standard * PREMIUM_SHIPPING_DISCOUNT_RATE) and
consolidation (consolidation_fee_standard * 1.00, since consolidation is
waived entirely for Premium, not a percentage rate). _make_shipment leaves
consolidation_fee_standard unset (None) by default, so it no-ops and doesn't
affect the shipping-only assertions below; a dedicated test below exercises
the consolidation component explicitly.
"""
import hashlib
import hmac
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Locker, User
from apps.locker.models import Batch
from apps.notifications.models import AppSettings
from apps.payments.models import BatchCharge, Payment
from apps.payments.views import _mark_batch_charges_paid, _record_shipment_premium_savings
from apps.shipments.models import Shipment


def _make_locker(email, plan_type='free'):
    user = User.objects.create(email=email, is_active=True)
    return Locker.objects.create(user=user, plan_type=plan_type)


def _make_shipment(user, shipping_cost_standard=Decimal('2000.00'), payment_status='unpaid', status='pending_payment',
                    consolidation_fee_standard=None, consolidation_fee=None):
    return Shipment.objects.create(
        user=user, shipment_type='international', status=status,
        payment_status=payment_status,
        recipient_name='Test Recipient', recipient_phone='9999999999',
        address_line1='Addr', city='Hyderabad', state='Telangana',
        postal_code='500001', country='India',
        shipping_cost_standard=shipping_cost_standard,
        shipping_cost=shipping_cost_standard,
        consolidation_fee_standard=consolidation_fee_standard,
        consolidation_fee=consolidation_fee,
    )


def _make_batch(locker):
    return Batch.objects.create(
        locker=locker, plan_type_at_creation=locker.plan_type, quota_year=2026,
        batch_status='active_chargeable', first_parcel_received_date=date(2026, 1, 1),
        current_parcel_count=1,
    )


def _make_batch_charge(batch, amount, amount_standard, charge_date, status='pending'):
    return BatchCharge.objects.create(
        batch=batch, charge_date=charge_date, parcel_count_snapshot=1,
        amount=amount, amount_standard=amount_standard, status=status,
    )


def _mock_invoice_upload():
    """The Shipment post_save signal (generate_invoice_on_paid) fires
    InvoiceService.generate_for_shipment whenever payment_status transitions
    into 'paid' — patch its Supabase upload so these tests never hit the
    network. Signal wraps the whole call in try/except so a raised mock
    wouldn't break the test either way, but this keeps intent explicit."""
    return patch('apps.payments.services.InvoiceService.upload_pdf', return_value='invoices/test.pdf')


# ---------------------------------------------------------------------------
# Call site #2: _record_shipment_premium_savings, direct
# ---------------------------------------------------------------------------

class RecordShipmentPremiumSavingsDirectTests(TestCase):
    def test_premium_locker_increments_by_shipping_standard_times_rate(self):
        locker = _make_locker('shipment-premium@example.com', plan_type='paid')
        shipment = _make_shipment(locker.user, shipping_cost_standard=Decimal('2000.00'))

        _record_shipment_premium_savings(shipment)

        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('100.00'))  # 2000 * 0.05

    def test_free_locker_still_increments_same_formula(self):
        locker = _make_locker('shipment-free@example.com', plan_type='free')
        shipment = _make_shipment(locker.user, shipping_cost_standard=Decimal('2000.00'))

        _record_shipment_premium_savings(shipment)

        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('100.00'))

    def test_no_shipping_cost_standard_does_not_increment(self):
        locker = _make_locker('shipment-no-standard@example.com', plan_type='paid')
        shipment = _make_shipment(locker.user, shipping_cost_standard=None)

        _record_shipment_premium_savings(shipment)

        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('0.00'))

    def test_consolidation_is_recorded_at_full_standard_amount(self):
        """Consolidation is 100% off for Premium, not a percentage rate —
        the full consolidation_fee_standard is credited, not a fraction."""
        locker = _make_locker('shipment-consolidation@example.com', plan_type='paid')
        shipment = _make_shipment(
            locker.user, shipping_cost_standard=Decimal('2000.00'),
            consolidation_fee_standard=Decimal('50.00'), consolidation_fee=Decimal('0.00'),
        )

        _record_shipment_premium_savings(shipment)

        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('150.00'))  # 100 (shipping) + 50 (consolidation)


# ---------------------------------------------------------------------------
# Call site #2: end-to-end through VerifyPaymentView / RazorpayWebhookView,
# including idempotency on a retried/duplicate capture.
# ---------------------------------------------------------------------------

class VerifyPaymentShipmentPremiumSavingsTests(TestCase):
    def setUp(self):
        self.locker = _make_locker('verify-shipment-premium@example.com', plan_type='paid')
        self.client.force_login(self.locker.user)
        self.shipment = _make_shipment(self.locker.user, shipping_cost_standard=Decimal('2000.00'))
        self.payment = Payment.objects.create(
            user=self.locker.user, amount=Decimal('1900.00'), payment_type='shipment',
            payment_method='razorpay', status='pending', razorpay_order_id='order_ship_1',
            shipment=self.shipment,
        )
        self.url = reverse('payments:verify')
        self.body = {
            'razorpay_order_id': 'order_ship_1',
            'razorpay_payment_id': 'pay_ship_1',
            'razorpay_signature': 'sig_ship_1',
        }

    def test_verify_increments_locker_savings_once(self):
        with patch('apps.payments.services.RazorpayService.verify_payment_signature', return_value=True), \
                _mock_invoice_upload():
            response = self.client.post(self.url, data=json.dumps(self.body), content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.payment_status, 'paid')
        self.locker.refresh_from_db()
        self.assertEqual(self.locker.premium_savings_amount, Decimal('100.00'))

    def test_double_verify_does_not_double_increment(self):
        with patch('apps.payments.services.RazorpayService.verify_payment_signature', return_value=True), \
                _mock_invoice_upload():
            first = self.client.post(self.url, data=json.dumps(self.body), content_type='application/json')
            second = self.client.post(self.url, data=json.dumps(self.body), content_type='application/json')

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.locker.refresh_from_db()
        self.assertEqual(self.locker.premium_savings_amount, Decimal('100.00'))


class VerifyPaymentBundledShipmentAndBatchChargesTests(TestCase):
    """Production shape: CreatePaymentOrderView bundles a locker's pending
    storage balance into the same shipment payment (see
    _get_pending_batch_charges_for_locker's docstring), so one verify call
    fires _mark_batch_charges_paid AND _record_shipment_premium_savings
    against the SAME locker row in the same request — exactly the
    same-second-clobber scenario record_premium_savings's docstring calls
    out ("a shipment payment and a storage-batch payment landing in the
    same second"). Both increments must land, and neither must overwrite
    the other's atomic F()-expression update."""

    def setUp(self):
        self.locker = _make_locker('verify-bundled@example.com', plan_type='paid')
        self.client.force_login(self.locker.user)
        self.shipment = _make_shipment(self.locker.user, shipping_cost_standard=Decimal('2000.00'))
        self.batch = _make_batch(self.locker)
        self.charge = _make_batch_charge(self.batch, Decimal('80.00'), Decimal('100.00'), date(2026, 1, 1))
        self.payment = Payment.objects.create(
            user=self.locker.user, amount=Decimal('1980.00'), payment_type='shipment',
            payment_method='razorpay', status='pending', razorpay_order_id='order_bundled_1',
            shipment=self.shipment,
            notes=json.dumps({'batch_charge_ids': [str(self.charge.pk)]}),
        )
        self.url = reverse('payments:verify')
        self.body = {
            'razorpay_order_id': 'order_bundled_1',
            'razorpay_payment_id': 'pay_bundled_1',
            'razorpay_signature': 'sig_bundled_1',
        }

    def test_both_increments_land_on_same_locker(self):
        with patch('apps.payments.services.RazorpayService.verify_payment_signature', return_value=True), \
                _mock_invoice_upload():
            response = self.client.post(self.url, data=json.dumps(self.body), content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.locker.refresh_from_db()
        # shipping: 2000 * 0.05 = 100.00 ; storage: 100 * 0.20 = 20.00
        self.assertEqual(self.locker.premium_savings_amount, Decimal('120.00'))
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.status, 'paid')

    def test_double_verify_does_not_double_increment_either_side(self):
        with patch('apps.payments.services.RazorpayService.verify_payment_signature', return_value=True), \
                _mock_invoice_upload():
            first = self.client.post(self.url, data=json.dumps(self.body), content_type='application/json')
            second = self.client.post(self.url, data=json.dumps(self.body), content_type='application/json')

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.locker.refresh_from_db()
        self.assertEqual(self.locker.premium_savings_amount, Decimal('120.00'))


class RazorpayWebhookShipmentPremiumSavingsTests(TestCase):
    def setUp(self):
        self.locker = _make_locker('webhook-shipment-premium@example.com', plan_type='paid')
        self.shipment = _make_shipment(self.locker.user, shipping_cost_standard=Decimal('2000.00'))
        self.payment = Payment.objects.create(
            user=self.locker.user, amount=Decimal('1900.00'), payment_type='shipment',
            payment_method='razorpay', status='pending', razorpay_order_id='order_ship_webhook_1',
            shipment=self.shipment,
        )
        settings = AppSettings.get_settings()
        settings.razorpay_webhook_secret = 'test_webhook_secret_shipment'
        settings.save()

        self.url = reverse('payments:razorpay_webhook')
        self.payload = json.dumps({
            'event': 'payment.captured',
            'payload': {'payment': {'entity': {'order_id': 'order_ship_webhook_1', 'id': 'pay_ship_webhook_1'}}},
        }).encode('utf-8')
        self.signature = hmac.new(
            b'test_webhook_secret_shipment', self.payload, hashlib.sha256
        ).hexdigest()

    def test_webhook_delivered_twice_increments_locker_savings_once(self):
        with _mock_invoice_upload():
            first = self.client.post(
                self.url, data=self.payload, content_type='application/json',
                HTTP_X_RAZORPAY_SIGNATURE=self.signature,
            )
            second = self.client.post(
                self.url, data=self.payload, content_type='application/json',
                HTTP_X_RAZORPAY_SIGNATURE=self.signature,
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.locker.refresh_from_db()
        self.assertEqual(self.locker.premium_savings_amount, Decimal('100.00'))


# ---------------------------------------------------------------------------
# Call site #3: _mark_batch_charges_paid — grouped-by-locker bulk increment,
# snapshotted before the bulk status flip.
# ---------------------------------------------------------------------------

class MarkBatchChargesPaidPremiumSavingsTests(TestCase):
    def test_single_locker_multiple_charges_sum_before_incrementing(self):
        locker = _make_locker('batch-single-locker@example.com', plan_type='paid')
        batch = _make_batch(locker)
        charge1 = _make_batch_charge(batch, Decimal('40.00'), Decimal('50.00'), date(2026, 1, 1))
        charge2 = _make_batch_charge(batch, Decimal('80.00'), Decimal('100.00'), date(2026, 1, 2))
        payment = Payment.objects.create(
            user=locker.user, amount=Decimal('120.00'), payment_type='storage_batch',
            payment_method='razorpay', status='captured',
            notes=json.dumps({'batch_charge_ids': [str(charge1.pk), str(charge2.pk)]}),
        )

        _mark_batch_charges_paid(payment)

        locker.refresh_from_db()
        # (50 + 100) * 0.20 = 30.00
        self.assertEqual(locker.premium_savings_amount, Decimal('30.00'))
        charge1.refresh_from_db()
        charge2.refresh_from_db()
        self.assertEqual(charge1.status, 'paid')
        self.assertEqual(charge2.status, 'paid')

    def test_free_locker_still_increments_same_formula(self):
        locker = _make_locker('batch-free-locker@example.com', plan_type='free')
        batch = _make_batch(locker)
        charge = _make_batch_charge(batch, Decimal('40.00'), Decimal('50.00'), date(2026, 1, 1))
        payment = Payment.objects.create(
            user=locker.user, amount=Decimal('40.00'), payment_type='storage_batch',
            payment_method='razorpay', status='captured',
            notes=json.dumps({'batch_charge_ids': [str(charge.pk)]}),
        )

        _mark_batch_charges_paid(payment)

        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('10.00'))  # 50 * 0.20

    def test_multi_locker_grouping_does_not_cross_contaminate(self):
        """A single payment's batch_charge_ids can span multiple lockers'
        batches (per _mark_batch_charges_paid's grouping) — each locker must
        get only its own charges' worth, never the other locker's."""
        locker_a = _make_locker('batch-multi-a@example.com', plan_type='paid')
        locker_b = _make_locker('batch-multi-b@example.com', plan_type='paid')
        batch_a = _make_batch(locker_a)
        batch_b = _make_batch(locker_b)
        charge_a = _make_batch_charge(batch_a, Decimal('40.00'), Decimal('50.00'), date(2026, 1, 1))
        charge_b = _make_batch_charge(batch_b, Decimal('160.00'), Decimal('200.00'), date(2026, 1, 1))
        payment = Payment.objects.create(
            user=locker_a.user, amount=Decimal('200.00'), payment_type='storage_batch',
            payment_method='razorpay', status='captured',
            notes=json.dumps({'batch_charge_ids': [str(charge_a.pk), str(charge_b.pk)]}),
        )

        _mark_batch_charges_paid(payment)

        locker_a.refresh_from_db()
        locker_b.refresh_from_db()
        self.assertEqual(locker_a.premium_savings_amount, Decimal('10.00'))   # 50 * 0.20
        self.assertEqual(locker_b.premium_savings_amount, Decimal('40.00'))  # 200 * 0.20

    def test_no_batch_charge_ids_is_noop(self):
        locker = _make_locker('batch-no-ids@example.com', plan_type='paid')
        payment = Payment.objects.create(
            user=locker.user, amount=Decimal('0.00'), payment_type='storage_batch',
            payment_method='razorpay', status='captured', notes='',
        )

        _mark_batch_charges_paid(payment)

        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('0.00'))

    def test_repeated_call_on_already_paid_charges_does_not_double_increment(self):
        """The bulk .update() only touches status='pending' rows, so a second
        call referencing already-paid charge ids finds nothing to snapshot —
        idempotent by construction of the query, not just an outer guard."""
        locker = _make_locker('batch-idempotent@example.com', plan_type='paid')
        batch = _make_batch(locker)
        charge = _make_batch_charge(batch, Decimal('40.00'), Decimal('50.00'), date(2026, 1, 1))
        payment = Payment.objects.create(
            user=locker.user, amount=Decimal('40.00'), payment_type='storage_batch',
            payment_method='razorpay', status='captured',
            notes=json.dumps({'batch_charge_ids': [str(charge.pk)]}),
        )

        _mark_batch_charges_paid(payment)
        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('10.00'))

        _mark_batch_charges_paid(payment)
        locker.refresh_from_db()
        self.assertEqual(locker.premium_savings_amount, Decimal('10.00'))


class VerifyPaymentBatchChargesPremiumSavingsEndToEndTests(TestCase):
    """_mark_batch_charges_paid's single-locker path exercised through the
    actual VerifyPaymentView.post entrypoint, including a retried/duplicate
    verify call (idempotency guarded by payment.status == 'captured')."""

    def setUp(self):
        self.locker = _make_locker('verify-batch-premium@example.com', plan_type='paid')
        self.client.force_login(self.locker.user)
        self.batch = _make_batch(self.locker)
        self.charge = _make_batch_charge(self.batch, Decimal('80.00'), Decimal('100.00'), date(2026, 1, 1))
        self.payment = Payment.objects.create(
            user=self.locker.user, amount=Decimal('80.00'), payment_type='storage_batch',
            payment_method='razorpay', status='pending', razorpay_order_id='order_batch_1',
            notes=json.dumps({'batch_charge_ids': [str(self.charge.pk)]}),
        )
        self.url = reverse('payments:verify')
        self.body = {
            'razorpay_order_id': 'order_batch_1',
            'razorpay_payment_id': 'pay_batch_1',
            'razorpay_signature': 'sig_batch_1',
        }

    def test_double_verify_does_not_double_increment(self):
        with patch('apps.payments.services.RazorpayService.verify_payment_signature', return_value=True):
            first = self.client.post(self.url, data=json.dumps(self.body), content_type='application/json')
            second = self.client.post(self.url, data=json.dumps(self.body), content_type='application/json')

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.locker.refresh_from_db()
        self.assertEqual(self.locker.premium_savings_amount, Decimal('20.00'))  # 100 * 0.20
