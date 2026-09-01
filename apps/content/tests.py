from decimal import Decimal
from django.test import TestCase
from apps.content.services import get_service_charge


class AddonServiceChargeSeedTests(TestCase):
    def test_all_four_addon_charges_are_seeded_and_active(self):
        insurance = get_service_charge('addon_insurance')
        self.assertIsNotNone(insurance)
        self.assertEqual(insurance.charge_type, 'percentage')
        self.assertEqual(insurance.percentage_rate, Decimal('2.00'))
        self.assertEqual(insurance.amount, Decimal('99.00'))

        for code, expected_amount in [
            ('addon_extra_photos', Decimal('149.00')),
            ('addon_priority_packing', Decimal('299.00')),
            ('addon_gift_wrapping', Decimal('99.00')),
        ]:
            charge = get_service_charge(code)
            self.assertIsNotNone(charge, f"{code} should be seeded")
            self.assertEqual(charge.charge_type, 'flat')
            self.assertEqual(charge.amount, expected_amount)
