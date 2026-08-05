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
