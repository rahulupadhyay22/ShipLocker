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
