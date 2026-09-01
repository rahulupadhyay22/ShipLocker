"""Tests for spec 13 (shipment add-ons) as it touches apps/content:
- KNOWN_SERVICE_CHARGE_CODES includes the four addon_* codes
- The four addon ServiceCharge rows are seeded, active, and priced per spec
- Pricing shape (flat vs percentage-with-floor) matches the spec's numbers
- Admin editability: changing a row's name/description/amount takes effect
  without a deploy (read straight from the DB / via get_service_charge)

Written independently of apps/content/tests.py's existing
AddonServiceChargeSeedTests -- some overlap in what's asserted is expected,
but these are fresh tests derived from the spec text, not copies.
"""
from decimal import Decimal

from django.test import TestCase

from apps.content.models import KNOWN_SERVICE_CHARGE_CODES, ServiceCharge
from apps.content.services import get_service_charge, invalidate_service_charge_cache


ADDON_CODES = [
    'addon_insurance',
    'addon_extra_photos',
    'addon_priority_packing',
    'addon_gift_wrapping',
]


class KnownServiceChargeCodesTests(TestCase):
    def test_all_four_addon_codes_are_registered(self):
        registered_codes = {code for code, _label in KNOWN_SERVICE_CHARGE_CODES}
        for code in ADDON_CODES:
            self.assertIn(code, registered_codes)

    def test_addon_codes_have_distinct_human_labels(self):
        labels = dict(KNOWN_SERVICE_CHARGE_CODES)
        seen = set()
        for code in ADDON_CODES:
            label = labels[code]
            self.assertTrue(label)
            self.assertNotIn(label, seen)
            seen.add(label)


class AddonServiceChargeSeedRowsTests(TestCase):
    """Direct ORM checks (bypassing the cache) that the seed migration wrote
    exactly the rows the spec describes."""

    def test_four_rows_exist_and_are_active(self):
        rows = ServiceCharge.objects.filter(code__in=ADDON_CODES)
        self.assertEqual(rows.count(), 4)
        for row in rows:
            self.assertTrue(row.is_active)

    def test_insurance_is_percentage_two_percent_floor_99(self):
        charge = ServiceCharge.objects.get(code='addon_insurance')
        self.assertEqual(charge.charge_type, 'percentage')
        self.assertEqual(charge.percentage_rate, Decimal('2.00'))
        self.assertEqual(charge.amount, Decimal('99.00'))

    def test_extra_photos_is_flat_149(self):
        charge = ServiceCharge.objects.get(code='addon_extra_photos')
        self.assertEqual(charge.charge_type, 'flat')
        self.assertEqual(charge.amount, Decimal('149.00'))

    def test_priority_packing_is_flat_299(self):
        charge = ServiceCharge.objects.get(code='addon_priority_packing')
        self.assertEqual(charge.charge_type, 'flat')
        self.assertEqual(charge.amount, Decimal('299.00'))

    def test_gift_wrapping_is_flat_99(self):
        charge = ServiceCharge.objects.get(code='addon_gift_wrapping')
        self.assertEqual(charge.charge_type, 'flat')
        self.assertEqual(charge.amount, Decimal('99.00'))


class AddonServiceChargeComputeShapeTests(TestCase):
    """ServiceCharge.compute() pricing shape for the addon rows, exercised
    directly against the DB rows (not via the payments-layer wrapper)."""

    def test_insurance_uses_floor_when_percentage_below_minimum(self):
        charge = ServiceCharge.objects.get(code='addon_insurance')
        # 2% of 1000 = 20, below the 99 floor
        self.assertEqual(charge.compute(Decimal('1000.00')), Decimal('99.00'))

    def test_insurance_uses_percentage_when_above_minimum(self):
        charge = ServiceCharge.objects.get(code='addon_insurance')
        # 2% of 10000 = 200, above the 99 floor
        self.assertEqual(charge.compute(Decimal('10000.00')), Decimal('200.00'))

    def test_flat_addon_ignores_product_value(self):
        charge = ServiceCharge.objects.get(code='addon_gift_wrapping')
        self.assertEqual(charge.compute(Decimal('999999.00')), Decimal('99.00'))
        self.assertEqual(charge.compute(), Decimal('99.00'))


class AddonServiceChargeAdminEditabilityTests(TestCase):
    """Spec: 'admin-editable afterward ... name/description/amount/
    percentage_rate/active state all editable with no deploy needed' and
    'Label/description shown to the customer come directly from the
    ServiceCharge row's own name/description fields ... an admin edit to
    either field takes effect in the wizard immediately, no deploy.'"""

    def setUp(self):
        self.addCleanup(invalidate_service_charge_cache, 'addon_gift_wrapping')

    def test_amount_edit_is_picked_up_by_get_service_charge(self):
        charge = ServiceCharge.objects.get(code='addon_gift_wrapping')
        charge.amount = Decimal('149.00')
        charge.save()
        invalidate_service_charge_cache('addon_gift_wrapping')

        fetched = get_service_charge('addon_gift_wrapping')
        self.assertEqual(fetched.amount, Decimal('149.00'))

    def test_name_and_description_edit_is_picked_up(self):
        charge = ServiceCharge.objects.get(code='addon_gift_wrapping')
        charge.name = 'Deluxe Gift Wrap'
        charge.description = 'Premium wrapping paper and ribbon.'
        charge.save()
        invalidate_service_charge_cache('addon_gift_wrapping')

        fetched = get_service_charge('addon_gift_wrapping')
        self.assertEqual(fetched.name, 'Deluxe Gift Wrap')
        self.assertEqual(fetched.description, 'Premium wrapping paper and ribbon.')

    def test_deactivating_row_makes_get_service_charge_return_none(self):
        charge = ServiceCharge.objects.get(code='addon_extra_photos')
        charge.is_active = False
        charge.save()
        invalidate_service_charge_cache('addon_extra_photos')
        self.addCleanup(invalidate_service_charge_cache, 'addon_extra_photos')

        self.assertIsNone(get_service_charge('addon_extra_photos'))

    def test_missing_code_returns_none(self):
        self.assertIsNone(get_service_charge('addon_does_not_exist'))
