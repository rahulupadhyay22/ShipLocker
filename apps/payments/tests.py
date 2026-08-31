from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase

from apps.payments.tax import calculate_gst


def _settings(company_state='Telangana', gst_rate_percent=Decimal('18.00')):
    return SimpleNamespace(company_state=company_state, gst_rate_percent=gst_rate_percent)


def _shipment(shipment_type='domestic', state='Telangana'):
    return SimpleNamespace(shipment_type=shipment_type, state=state)


class CalculateGstTests(TestCase):
    def test_international_is_zero_rated(self):
        result = calculate_gst(_shipment(shipment_type='international'), Decimal('1000.00'), _settings())
        self.assertTrue(result['is_zero_rated'])
        self.assertEqual(result['gst_rate'], Decimal('0.00'))
        self.assertEqual(result['cgst_amount'], Decimal('0.00'))
        self.assertEqual(result['sgst_amount'], Decimal('0.00'))
        self.assertEqual(result['igst_amount'], Decimal('0.00'))
        self.assertEqual(result['total_amount'], Decimal('1000.00'))

    def test_domestic_same_state_splits_cgst_sgst(self):
        result = calculate_gst(
            _shipment(shipment_type='domestic', state='Telangana'),
            Decimal('1000.00'),
            _settings(company_state='Telangana', gst_rate_percent=Decimal('18.00')),
        )
        self.assertFalse(result['is_zero_rated'])
        self.assertEqual(result['cgst_amount'], Decimal('90.00'))
        self.assertEqual(result['sgst_amount'], Decimal('90.00'))
        self.assertEqual(result['igst_amount'], Decimal('0.00'))
        self.assertEqual(result['total_amount'], Decimal('1180.00'))

    def test_domestic_same_state_case_and_whitespace_insensitive(self):
        result = calculate_gst(
            _shipment(shipment_type='domestic', state='  telangana  '),
            Decimal('1000.00'),
            _settings(company_state='TELANGANA', gst_rate_percent=Decimal('18.00')),
        )
        self.assertEqual(result['cgst_amount'], Decimal('90.00'))
        self.assertEqual(result['sgst_amount'], Decimal('90.00'))
        self.assertEqual(result['igst_amount'], Decimal('0.00'))

    def test_domestic_different_state_uses_igst(self):
        result = calculate_gst(
            _shipment(shipment_type='domestic', state='Maharashtra'),
            Decimal('1000.00'),
            _settings(company_state='Telangana', gst_rate_percent=Decimal('18.00')),
        )
        self.assertEqual(result['cgst_amount'], Decimal('0.00'))
        self.assertEqual(result['sgst_amount'], Decimal('0.00'))
        self.assertEqual(result['igst_amount'], Decimal('180.00'))
        self.assertEqual(result['total_amount'], Decimal('1180.00'))


from datetime import datetime

from django.utils import timezone

from apps.payments.services import generate_invoice_number
from apps.payments.models import Invoice
from apps.accounts.models import User
from apps.shipments.models import Shipment


def _make_shipment(user):
    return Shipment.objects.create(
        user=user, shipment_type='domestic', status='declaration_pending',
        recipient_name='Test Recipient', recipient_phone='9999999999',
        address_line1='Addr', city='Hyderabad', state='Telangana',
        postal_code='500001', country='India',
    )


class GenerateInvoiceNumberTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='invoice-number-test@example.com', is_active=True)

    def test_sequential_within_same_financial_year(self):
        first_date = timezone.make_aware(datetime(2026, 6, 1))
        second_date = timezone.make_aware(datetime(2026, 9, 1))

        first_number = generate_invoice_number(first_date)
        Invoice.objects.create(
            shipment=_make_shipment(self.user), invoice_number=first_number,
            invoice_date=first_date, customer_name='A', billing_address='addr',
            amount_paid=Decimal('100.00'), taxable_amount=Decimal('100.00'), total_amount=Decimal('100.00'),
        )
        second_number = generate_invoice_number(second_date)

        self.assertEqual(first_number, 'INV/2026-27/0001')
        self.assertEqual(second_number, 'INV/2026-27/0002')

    def test_new_financial_year_resets_prefix(self):
        fy_2026_date = timezone.make_aware(datetime(2026, 6, 1))
        fy_2027_date = timezone.make_aware(datetime(2027, 5, 1))  # FY 2027-28, since FY starts Apr 1

        fy_2026_number = generate_invoice_number(fy_2026_date)
        Invoice.objects.create(
            shipment=_make_shipment(self.user), invoice_number=fy_2026_number,
            invoice_date=fy_2026_date, customer_name='A', billing_address='addr',
            amount_paid=Decimal('100.00'), taxable_amount=Decimal('100.00'), total_amount=Decimal('100.00'),
        )
        fy_2027_number = generate_invoice_number(fy_2027_date)

        self.assertEqual(fy_2026_number, 'INV/2026-27/0001')
        self.assertEqual(fy_2027_number, 'INV/2027-28/0001')

    def test_march_is_still_previous_financial_year(self):
        march_date = timezone.make_aware(datetime(2027, 3, 15))
        number = generate_invoice_number(march_date)
        self.assertEqual(number, 'INV/2026-27/0001')


from unittest.mock import patch

from apps.notifications.models import AppSettings
from apps.payments.models import Payment
from apps.shipments.models import ShipmentDocument
from apps.payments.services import InvoiceService


class InvoiceServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='invoice-service-test@example.com', is_active=True)
        settings = AppSettings.get_settings()
        settings.company_legal_name = 'CamelTrunk Logistics Pvt Ltd'
        settings.company_gstin = '36AAAAA0000A1Z5'
        settings.company_pan = 'AAAAA0000A'
        settings.company_registered_address = 'Hyderabad, Telangana, India'
        settings.company_state = 'Telangana'
        settings.gst_rate_percent = Decimal('18.00')
        settings.save()

        self.shipment = _make_shipment(self.user)
        self.shipment.shipping_cost = Decimal('1000.00')
        self.shipment.save()

        Payment.objects.create(
            user=self.user, shipment=self.shipment, amount=Decimal('1000.00'),
            payment_method='razorpay', status='captured',
            razorpay_payment_id='pay_test123', paid_at=timezone.now(),
        )

        # Mark paid via update() (not save()) so these tests control
        # InvoiceService invocation directly, independent of the
        # payment_status signal wired up in Task 7.
        Shipment.objects.filter(pk=self.shipment.pk).update(payment_status='paid')
        self.shipment.refresh_from_db()

    def test_generate_for_shipment_creates_invoice_and_document(self):
        invoice = InvoiceService.generate_for_shipment(self.shipment, paid_at=timezone.now())

        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.shipment, self.shipment)
        self.assertTrue(invoice.invoice_number.startswith('INV/'))
        self.assertEqual(invoice.customer_name, 'Test Recipient')
        self.assertEqual(invoice.payment_reference, 'pay_test123')
        self.assertEqual(invoice.shipping_amount, Decimal('1000.00'))
        self.assertEqual(invoice.cgst_amount, Decimal('90.00'))
        self.assertEqual(invoice.sgst_amount, Decimal('90.00'))
        self.assertTrue(invoice.pdf_document_url)

        doc = ShipmentDocument.objects.get(shipment=self.shipment, document_type='invoice')
        self.assertEqual(doc.document_url, invoice.pdf_document_url)

    def test_generate_for_shipment_is_idempotent(self):
        first = InvoiceService.generate_for_shipment(self.shipment, paid_at=timezone.now())
        second = InvoiceService.generate_for_shipment(self.shipment, paid_at=timezone.now())

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Invoice.objects.filter(shipment=self.shipment).count(), 1)
        self.assertEqual(ShipmentDocument.objects.filter(shipment=self.shipment, document_type='invoice').count(), 1)

    def test_upload_failure_leaves_no_partial_record(self):
        with patch('apps.payments.services.InvoiceService.upload_pdf', side_effect=Exception('Supabase timeout')):
            with self.assertRaises(Exception):
                InvoiceService.generate_for_shipment(self.shipment, paid_at=timezone.now())

        self.assertEqual(Invoice.objects.filter(shipment=self.shipment).count(), 0)
        self.assertEqual(ShipmentDocument.objects.filter(shipment=self.shipment, document_type='invoice').count(), 0)


class ShipmentPaidSignalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='signal-test@example.com', is_active=True)
        settings = AppSettings.get_settings()
        settings.company_state = 'Telangana'
        settings.gst_rate_percent = Decimal('18.00')
        settings.save()

        self.shipment = _make_shipment(self.user)
        self.shipment.shipping_cost = Decimal('500.00')
        self.shipment.save()

    def test_marking_shipment_paid_generates_invoice(self):
        self.assertEqual(Invoice.objects.filter(shipment=self.shipment).count(), 0)

        self.shipment.payment_status = 'paid'
        self.shipment.paid_at = timezone.now()
        self.shipment.save()

        self.assertEqual(Invoice.objects.filter(shipment=self.shipment).count(), 1)

    def test_saving_an_already_paid_shipment_again_does_not_duplicate(self):
        self.shipment.payment_status = 'paid'
        self.shipment.paid_at = timezone.now()
        self.shipment.save()
        self.assertEqual(Invoice.objects.filter(shipment=self.shipment).count(), 1)

        # Unrelated field change while already paid — must not regenerate
        self.shipment.admin_notes = 'unrelated edit'
        self.shipment.save()
        self.assertEqual(Invoice.objects.filter(shipment=self.shipment).count(), 1)

    def test_invoice_generation_failure_does_not_raise_out_of_save(self):
        with patch('apps.payments.services.InvoiceService.upload_pdf', side_effect=Exception('Supabase down')):
            self.shipment.payment_status = 'paid'
            self.shipment.paid_at = timezone.now()
            self.shipment.save()  # must not raise

        self.assertEqual(Invoice.objects.filter(shipment=self.shipment).count(), 0)


from apps.accounts.models import Locker
from apps.payments.models import PersonalShopInvoice
from apps.payments.services import PersonalShopInvoiceService, generate_personal_shop_invoice_number
from apps.personal_shop.models import PersonalShopRequest, PersonalShopQuotation


def _make_personal_shop_request(user):
    locker = Locker.objects.create(user=user)
    return PersonalShopRequest.objects.create(
        locker=locker, request_type='custom_request', status='searching',
    )


def _make_personal_shop_quotation(req, **extra):
    return PersonalShopQuotation.objects.create(
        request=req, subtotal=Decimal('1000.00'), service_fee_amount=Decimal('100.00'),
        total_amount=Decimal('1100.00'), valid_until=timezone.now() + timedelta(hours=48),
        **extra,
    )


class PersonalShopInvoiceServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='ps-invoice-test@example.com', is_active=True)
        self.request = _make_personal_shop_request(self.user)
        self.quotation = _make_personal_shop_quotation(self.request)
        self.request.active_quotation = self.quotation
        self.request.save()

        Payment.objects.create(
            user=self.user, personal_shop_request=self.request, amount=Decimal('1100.00'),
            payment_method='razorpay', status='captured',
            razorpay_payment_id='pay_ps_test123', paid_at=timezone.now(),
        )

    def test_generate_for_request_creates_invoice(self):
        invoice = PersonalShopInvoiceService.generate_for_request(self.request)

        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.quotation, self.quotation)
        self.assertTrue(invoice.invoice_number.startswith('TA-INV/'))
        self.assertEqual(invoice.total_amount, Decimal('1100.00'))
        self.assertEqual(invoice.payment_reference, 'pay_ps_test123')
        self.assertTrue(invoice.pdf_document_url)

    def test_generate_for_request_is_idempotent(self):
        first = PersonalShopInvoiceService.generate_for_request(self.request)
        second = PersonalShopInvoiceService.generate_for_request(self.request)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PersonalShopInvoice.objects.filter(quotation=self.quotation).count(), 1)

    def test_upload_failure_leaves_no_partial_record(self):
        with patch('apps.payments.services.PersonalShopInvoiceService.upload_pdf', side_effect=Exception('Supabase timeout')):
            with self.assertRaises(Exception):
                PersonalShopInvoiceService.generate_for_request(self.request)

        self.assertEqual(PersonalShopInvoice.objects.filter(quotation=self.quotation).count(), 0)


class PersonalShopRequestPaidSignalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='ps-signal-test@example.com', is_active=True)
        self.request = _make_personal_shop_request(self.user)
        self.quotation = _make_personal_shop_quotation(self.request)
        self.request.active_quotation = self.quotation
        self.request.save()

    def test_marking_request_paid_generates_invoice(self):
        self.assertEqual(PersonalShopInvoice.objects.filter(quotation=self.quotation).count(), 0)

        self.request.status = 'paid'
        self.request.save()

        self.assertEqual(PersonalShopInvoice.objects.filter(quotation=self.quotation).count(), 1)

    def test_saving_an_already_paid_request_again_does_not_duplicate(self):
        self.request.status = 'paid'
        self.request.save()
        self.assertEqual(PersonalShopInvoice.objects.filter(quotation=self.quotation).count(), 1)

        self.request.refund_required = False
        self.request.save()
        self.assertEqual(PersonalShopInvoice.objects.filter(quotation=self.quotation).count(), 1)

    def test_invoice_generation_failure_does_not_raise_out_of_save(self):
        with patch(
            'apps.payments.services.PersonalShopInvoiceService.upload_pdf',
            side_effect=Exception('Supabase down'),
        ):
            self.request.status = 'paid'
            self.request.save()  # must not raise

        self.assertEqual(PersonalShopInvoice.objects.filter(quotation=self.quotation).count(), 0)


from django.urls import reverse as django_reverse


class CreatePaymentOrderConsolidationFeeTests(TestCase):
    """Regression test for the consolidation_fee billing gap: total_due
    must include consolidation_fee, not just shipping + pending storage."""

    def setUp(self):
        self.user = User.objects.create(email='consolidation-bug@example.com', is_active=True)
        self.locker = Locker.objects.create(user=self.user, plan_type='free')
        self.shipment = Shipment.objects.create(
            user=self.user,
            shipment_type='international',
            status='pending_payment',
            recipient_name='Jane Doe',
            address_line1='1 Test Street',
            city='Testville',
            state='Test State',
            postal_code='12345',
            country='United States',
            shipping_cost=Decimal('800.00'),
            consolidation_fee=Decimal('300.00'),
            currency='INR',
        )
        self.client.force_login(self.user)
        self.url = django_reverse('payments:create_order', kwargs={'shipment_pk': self.shipment.pk})

    def test_total_due_includes_consolidation_fee(self):
        p1, p2, p3 = _enable_razorpay('order_consolidation_1')
        with p1, p2, p3:
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        # 800 shipping + 300 consolidation = 1100.00 -> 110000 paise
        self.assertEqual(data['amount'], 110000)

        payment = Payment.objects.get(shipment=self.shipment)
        self.assertEqual(payment.amount, Decimal('1100.00'))


from datetime import date

from apps.locker.models import Batch
from apps.payments.models import BatchCharge


def _make_batch_charge(locker, amount, amount_standard, charge_date=None):
    batch = Batch.objects.create(
        locker=locker, plan_type_at_creation=locker.plan_type, quota_year=2026,
        batch_status='active_chargeable', first_parcel_received_date=date(2026, 1, 1),
        free_storage_end_date=None, current_parcel_count=1,
    )
    return BatchCharge.objects.create(
        batch=batch, charge_date=charge_date or date(2026, 1, 1),
        parcel_count_snapshot=1, amount=amount, amount_standard=amount_standard,
    )


class BatchChargeDiscountAmountTests(TestCase):
    """BatchCharge.discount_amount: 0 for pre-existing rows (amount_standard
    is NULL), computed correctly when both fields are set, never negative."""

    def setUp(self):
        self.user = User.objects.create(email='batchcharge-discount@example.com', is_active=True)
        self.locker = Locker.objects.create(user=self.user, plan_type='paid')

    def test_amount_standard_none_returns_zero(self):
        charge = _make_batch_charge(self.locker, amount=Decimal('100.00'), amount_standard=None)
        self.assertEqual(charge.discount_amount, Decimal('0.00'))

    def test_computes_discount_when_both_set(self):
        charge = _make_batch_charge(self.locker, amount=Decimal('80.00'), amount_standard=Decimal('100.00'))
        self.assertEqual(charge.discount_amount, Decimal('20.00'))

    def test_never_negative(self):
        # amount > amount_standard should never happen in practice, but the
        # property must not return a negative value if it somehow does.
        charge = _make_batch_charge(self.locker, amount=Decimal('120.00'), amount_standard=Decimal('100.00'))
        self.assertEqual(charge.discount_amount, Decimal('0.00'))


# ---------------------------------------------------------------------------
# Task 5: Premium subscription checkout & renewal lifecycle
# ---------------------------------------------------------------------------

import hmac
import hashlib
import json
from unittest.mock import PropertyMock

from django.urls import reverse

from apps.payments.services import RazorpayService
from apps.payments.views import _activate_premium_subscription
from apps.locker.services import batch_billing


def _enable_razorpay(order_id='order_premium_test'):
    """Returns the three patch objects needed to make RazorpayService
    behave as configured+enabled, with create_order returning a fixed
    order id. Caller enters them as a context manager / decorator."""
    return (
        patch('apps.payments.services.RazorpayService.is_enabled', new_callable=PropertyMock, return_value=True),
        patch('apps.payments.services.RazorpayService.key_id', new_callable=PropertyMock, return_value='rzp_test_key'),
        patch('apps.payments.services.RazorpayService.create_order', return_value={'id': order_id}),
    )


class CreatePremiumSubscriptionOrderViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='premium-checkout@example.com', is_active=True)
        self.locker = Locker.objects.create(user=self.user)
        self.client.force_login(self.user)
        settings = AppSettings.get_settings()
        settings.premium_annual_price = Decimal('2999.00')
        settings.save()
        self.url = reverse('payments:premium_create_order')

    def test_creates_order_and_pending_payment(self):
        p1, p2, p3 = _enable_razorpay('order_premium_1')
        with p1, p2, p3:
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['order_id'], 'order_premium_1')
        self.assertEqual(data['amount'], 299900)

        payment = Payment.objects.get(user=self.user, payment_type='premium_subscription')
        self.assertEqual(payment.status, 'pending')
        self.assertEqual(payment.amount, Decimal('2999.00'))
        self.assertEqual(payment.razorpay_order_id, 'order_premium_1')

    def test_duplicate_post_within_window_returns_same_order(self):
        p1, p2, p3 = _enable_razorpay('order_premium_2')
        with p1, p2, p3:
            first = self.client.post(self.url)
            second = self.client.post(self.url)

        self.assertEqual(first.json()['order_id'], second.json()['order_id'])
        self.assertEqual(
            Payment.objects.filter(user=self.user, payment_type='premium_subscription').count(), 1
        )


class ActivatePremiumSubscriptionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='premium-activate@example.com', is_active=True)
        self.locker = Locker.objects.create(user=self.user)

    def _payment(self):
        return Payment.objects.create(
            user=self.user, amount=Decimal('2999.00'), payment_type='premium_subscription',
            payment_method='razorpay', status='captured', paid_at=timezone.now(),
        )

    def test_first_time_purchase_sets_plan_and_expiry(self):
        today = timezone.localdate()
        _activate_premium_subscription(self._payment())

        self.locker.refresh_from_db()
        self.assertEqual(self.locker.plan_type, 'paid')
        self.assertEqual(self.locker.premium_expires_at, today + timedelta(days=365))

    def test_early_renewal_extends_from_current_expiry_not_today(self):
        today = timezone.localdate()
        self.locker.plan_type = 'paid'
        self.locker.premium_expires_at = today + timedelta(days=100)
        self.locker.save()

        _activate_premium_subscription(self._payment())

        self.locker.refresh_from_db()
        self.assertEqual(self.locker.premium_expires_at, today + timedelta(days=100 + 365))

    def test_renewal_during_grace_period_resolves_grace_and_pending_batch(self):
        today = timezone.localdate()
        self.locker.plan_type = 'paid'
        self.locker.premium_expires_at = today - timedelta(days=2)
        self.locker.save()
        batch_billing.enter_grace_period(self.locker, today)
        self.locker.refresh_from_db()
        self.assertIsNotNone(self.locker.payment_grace_until)

        batch = Batch.objects.create(
            locker=self.locker, plan_type_at_creation='paid', quota_year=today.year,
            batch_status='pending', first_parcel_received_date=today,
            free_storage_end_date=today + timedelta(days=30), current_parcel_count=1,
        )

        _activate_premium_subscription(self._payment())

        self.locker.refresh_from_db()
        batch.refresh_from_db()
        self.assertIsNone(self.locker.payment_grace_until)
        self.assertEqual(self.locker.plan_type, 'paid')
        self.assertEqual(self.locker.premium_expires_at, today + timedelta(days=365))
        self.assertEqual(batch.batch_status, 'active_free')


class VerifyPaymentViewPremiumIdempotencyTests(TestCase):
    """The most important test in this task: VerifyPaymentView.post must
    not double-extend premium_expires_at if called twice for the same
    already-captured payment (retried/double-fired verify request)."""

    def setUp(self):
        self.user = User.objects.create(email='premium-verify@example.com', is_active=True)
        self.locker = Locker.objects.create(user=self.user)
        self.client.force_login(self.user)
        self.payment = Payment.objects.create(
            user=self.user, amount=Decimal('2999.00'), payment_type='premium_subscription',
            payment_method='razorpay', status='pending', razorpay_order_id='order_verify_1',
        )
        self.url = reverse('payments:verify')
        self.body = {
            'razorpay_order_id': 'order_verify_1',
            'razorpay_payment_id': 'pay_verify_1',
            'razorpay_signature': 'sig_verify_1',
        }

    def test_double_verify_extends_expiry_exactly_once(self):
        today = timezone.localdate()
        with patch('apps.payments.services.RazorpayService.verify_payment_signature', return_value=True):
            first = self.client.post(self.url, data=json.dumps(self.body), content_type='application/json')
            second = self.client.post(self.url, data=json.dumps(self.body), content_type='application/json')

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()['status'], 'success')
        self.assertEqual(second.json()['status'], 'success')

        self.locker.refresh_from_db()
        self.assertEqual(self.locker.plan_type, 'paid')
        self.assertEqual(self.locker.premium_expires_at, today + timedelta(days=365))

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'captured')


class RazorpayWebhookPremiumDoubleDeliveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='premium-webhook@example.com', is_active=True)
        self.locker = Locker.objects.create(user=self.user)
        self.payment = Payment.objects.create(
            user=self.user, amount=Decimal('2999.00'), payment_type='premium_subscription',
            payment_method='razorpay', status='pending', razorpay_order_id='order_webhook_1',
        )
        settings = AppSettings.get_settings()
        settings.razorpay_webhook_secret = 'test_webhook_secret'
        settings.save()

        self.url = reverse('payments:razorpay_webhook')
        self.payload = json.dumps({
            'event': 'payment.captured',
            'payload': {'payment': {'entity': {'order_id': 'order_webhook_1', 'id': 'pay_webhook_1'}}},
        }).encode('utf-8')
        self.signature = hmac.new(
            b'test_webhook_secret', self.payload, hashlib.sha256
        ).hexdigest()

    def test_same_webhook_delivered_twice_extends_expiry_once(self):
        today = timezone.localdate()
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
        self.assertEqual(self.locker.plan_type, 'paid')
        self.assertEqual(self.locker.premium_expires_at, today + timedelta(days=365))


import threading

from django.test import Client, TransactionTestCase, skipUnlessDBFeature


@skipUnlessDBFeature('has_select_for_update')
class RazorpayWebhookConcurrentCaptureTests(TransactionTestCase):
    """Genuine concurrency test for the RazorpayWebhookView.post fix (lock ->
    check -> mutate, mirroring VerifyPaymentView), NOT just sequential
    double-delivery (RazorpayWebhookPremiumDoubleDeliveryTests above already
    covers that and is not what's broken here).

    This repo's test database is a real hosted Postgres (Supabase, reached
    via DATABASE_POOLER_URL / PgBouncer transaction pooling) — confirmed
    empirically by running `manage.py test --keepdb`, which connects
    successfully and runs migrations against it. Row-level locks taken with
    select_for_update() are therefore real locks enforced by Postgres, not a
    SQLite no-op. This test relies on that: it uses two real threads (each
    gets its own DB connection, as Django connections are thread-local) and
    TransactionTestCase (so writes actually commit and are visible across
    connections, unlike plain TestCase's per-test rollback).

    Mechanism: _mark_batch_charges_paid is patched so the first caller to
    reach it (i.e. the first thread to win the SELECT ... FOR UPDATE lock and
    enter the atomic block) blocks there on an Event, holding its transaction
    -- and therefore the row lock -- open. Only once that thread is confirmed
    to be holding the lock is the second thread started; its own
    select_for_update() then must genuinely block at the database level until
    the first thread's transaction commits. This is what would fail (both
    threads reading status='pending' and both calling
    _activate_premium_subscription, double-extending premium_expires_at) on
    the unfixed code -- the unfixed code did the status check with a plain
    unlocked .get() before ever entering transaction.atomic(), so there was no
    lock to block thread two.

    Environment caveat: being a TransactionTestCase, this class flushes every
    table on teardown instead of rolling back (Django orders TransactionTestCase
    classes after plain TestCase classes within one run, so this is harmless
    within a single `manage.py test` invocation). But this repo's Postgres
    test database can only be created with `--keepdb` (creating it fresh fails
    on a template1 collation mismatch unrelated to this feature), so the flush
    from this class persists into the *next* invocation's test database and
    wipes data-migration-seeded rows other tests depend on (notably
    apps/content/migrations/0010_seed_service_charge_codes.py's ServiceCharge
    rows, which apps.personal_shop's SuggestedServiceFeePricingTests reads).
    If a fresh `--keepdb` run shows unrelated pricing-lookup tests returning
    None, re-apply that migration's seed_charges(apps, None) against the kept
    test_postgres database before re-running."""

    @classmethod
    def tearDownClass(cls):
        # TransactionTestCase truncates every table (not just the ones this
        # class writes to) once its last test method finishes — including
        # apps.content.ServiceCharge rows seeded by data migration
        # apps/content/migrations/0010_seed_service_charge_codes.py, which
        # apps.personal_shop's pricing tests depend on. Restore that seed
        # data here (same codes/amounts, get_or_create so this is a no-op
        # if the rows already exist) so this class doesn't leave the
        # suite's shared fixtures broken for whatever test runs next.
        super().tearDownClass()
        from apps.content.models import ServiceCharge

        for code, amount, charge_type, rate in [
            ('trunkassist_product_link', '199.00', 'percentage', '5.00'),
            ('trunkassist_image_search', '299.00', 'percentage', '6.00'),
            ('trunkassist_cart_screenshot', '299.00', 'percentage', '6.00'),
            ('trunkassist_boutique_purchase', '399.00', 'percentage', '7.00'),
            ('trunkassist_local_shop_purchase', '499.00', 'flat', None),
            ('trunkassist_custom_request', '499.00', 'flat', None),
        ]:
            ServiceCharge.objects.get_or_create(
                code=code,
                defaults={
                    'name': code, 'charge_type': charge_type, 'percentage_rate': rate,
                    'amount': amount, 'currency': 'INR', 'is_active': True,
                },
            )

    def setUp(self):
        self.user = User.objects.create(email='premium-webhook-race@example.com', is_active=True)
        self.locker = Locker.objects.create(user=self.user)
        self.payment = Payment.objects.create(
            user=self.user, amount=Decimal('2999.00'), payment_type='premium_subscription',
            payment_method='razorpay', status='pending', razorpay_order_id='order_webhook_race',
        )
        settings = AppSettings.get_settings()
        settings.razorpay_webhook_secret = 'test_webhook_secret_race'
        settings.save()

        self.url = reverse('payments:razorpay_webhook')
        self.payload = json.dumps({
            'event': 'payment.captured',
            'payload': {'payment': {'entity': {'order_id': 'order_webhook_race', 'id': 'pay_webhook_race'}}},
        }).encode('utf-8')
        self.signature = hmac.new(
            b'test_webhook_secret_race', self.payload, hashlib.sha256
        ).hexdigest()

    def test_concurrent_webhook_deliveries_extend_expiry_only_once(self):
        from apps.payments.views import _mark_batch_charges_paid as real_mark_batch_charges_paid

        today = timezone.localdate()
        locked_event = threading.Event()
        release_event = threading.Event()

        def paced_mark_batch_charges_paid(payment):
            # Signal that we're inside the atomic block holding the row lock,
            # then wait so the second thread's select_for_update() has time
            # to hit the database and genuinely block on that lock.
            locked_event.set()
            release_event.wait(timeout=5)
            return real_mark_batch_charges_paid(payment)

        results = {}

        def deliver(name):
            client = Client()
            response = client.post(
                self.url, data=self.payload, content_type='application/json',
                HTTP_X_RAZORPAY_SIGNATURE=self.signature,
            )
            results[name] = response.status_code

        with patch('apps.payments.views._mark_batch_charges_paid', side_effect=paced_mark_batch_charges_paid) as mock_mark:
            t1 = threading.Thread(target=deliver, args=('t1',))
            t1.start()

            # Wait until t1 is inside the locked transaction before starting t2.
            self.assertTrue(locked_event.wait(timeout=5), "t1 never reached the locked section")
            t2 = threading.Thread(target=deliver, args=('t2',))
            t2.start()

            # Give t2 a moment to actually issue its SELECT ... FOR UPDATE and
            # block on it at the database level, then let t1 finish + commit.
            import time
            time.sleep(0.5)
            release_event.set()

            t1.join(timeout=10)
            t2.join(timeout=10)

        self.assertEqual(results.get('t1'), 200)
        self.assertEqual(results.get('t2'), 200)
        # Only the winner of the lock should have run the capture/dispatch path.
        self.assertEqual(mock_mark.call_count, 1)

        self.locker.refresh_from_db()
        self.assertEqual(self.locker.plan_type, 'paid')
        self.assertEqual(self.locker.premium_expires_at, today + timedelta(days=365))

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'captured')
