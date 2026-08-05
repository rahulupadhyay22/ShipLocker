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
