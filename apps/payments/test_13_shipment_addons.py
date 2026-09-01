"""Tests for spec 13 (shipment add-ons), apps/payments scope:
CreatePaymentOrderView total_due wiring for add-ons, the displayed-vs-charged
equivalence property, Invoice.addons_amount/GST inclusion, the paid-shipment
no-double-charge behavior, and description/notes labeling correctness.

Written independently of the existing add-on coverage in apps/payments/
tests.py (ComputeAddonAmountTests, GetAddonOptionsTests,
CreatePaymentOrderAddonsTests, etc.) -- some overlap in what's asserted is
expected since both are derived from the same spec, but scenarios here are
not copy-pasted from that file.
"""
import json
from datetime import date
from decimal import Decimal
from unittest.mock import PropertyMock, patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User, Locker
from apps.locker.models import Batch
from apps.payments.models import BatchCharge, Payment
from apps.payments.services import InvoiceService
from apps.shipments.models import Shipment, ShipmentAddon
from apps.shipments.views import _payment_summary


def _enable_razorpay(order_id='order_addon_test'):
    return (
        patch('apps.payments.services.RazorpayService.is_enabled', new_callable=PropertyMock, return_value=True),
        patch('apps.payments.services.RazorpayService.key_id', new_callable=PropertyMock, return_value='rzp_test_key'),
        patch('apps.payments.services.RazorpayService.create_order', return_value={'id': order_id}),
    )


def make_shipment(user, **overrides):
    defaults = dict(
        user=user,
        shipment_type='international',
        status='pending_payment',
        recipient_name='Jane Doe',
        address_line1='1 Test Street',
        city='Testville',
        state='Test State',
        postal_code='12345',
        country='United States',
        currency='INR',
    )
    defaults.update(overrides)
    return Shipment.objects.create(**defaults)


def make_batch_charge(locker, amount, charge_date=None):
    batch = Batch.objects.create(
        locker=locker, plan_type_at_creation=locker.plan_type, quota_year=2026,
        batch_status='active_chargeable', first_parcel_received_date=date(2026, 1, 1),
        free_storage_end_date=None, current_parcel_count=1,
    )
    return BatchCharge.objects.create(
        batch=batch, charge_date=charge_date or date(2026, 1, 1),
        parcel_count_snapshot=1, amount=amount, amount_standard=amount,
    )


# ---------------------------------------------------------------------------
# CreatePaymentOrderView -- total_due wiring for add-ons
# ---------------------------------------------------------------------------

class CreatePaymentOrderAddonsWiringTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='addon-wiring@example.com', is_active=True)
        self.locker = Locker.objects.create(user=self.user, plan_type='free')
        self.shipment = make_shipment(
            self.user,
            shipping_cost=Decimal('800.00'),
            consolidation_fee=Decimal('300.00'),
        )
        ShipmentAddon.objects.create(shipment=self.shipment, code='gift_wrapping', amount=Decimal('99.00'))
        ShipmentAddon.objects.create(shipment=self.shipment, code='priority_packing', amount=Decimal('299.00'))
        self.client.force_login(self.user)
        self.url = reverse('payments:create_order', kwargs={'shipment_pk': self.shipment.pk})

    def test_total_due_includes_shipping_consolidation_and_addons(self):
        p1, p2, p3 = _enable_razorpay('order_wiring_1')
        with p1, p2, p3:
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        # 800 shipping + 300 consolidation + 398 addons = 1498.00 -> 149800 paise
        self.assertEqual(response.json()['amount'], 149800)
        payment = Payment.objects.get(shipment=self.shipment)
        self.assertEqual(payment.amount, Decimal('1498.00'))

    def test_notes_carry_addons_due_separately_from_consolidation_due(self):
        p1, p2, p3 = _enable_razorpay('order_wiring_2')
        with p1, p2, p3:
            self.client.post(self.url)

        payment = Payment.objects.get(shipment=self.shipment)
        notes = json.loads(payment.notes)
        self.assertEqual(notes['consolidation_due'], '300.00')
        self.assertEqual(notes['addons_due'], '398.00')

    def test_description_mentions_addons_and_consolidation_as_distinct_parts(self):
        p1, p2, p3 = _enable_razorpay('order_wiring_3')
        with p1, p2, p3:
            self.client.post(self.url)

        payment = Payment.objects.get(shipment=self.shipment)
        description_lower = payment.description.lower()
        self.assertIn('add-ons', description_lower)
        self.assertIn('consolidation', description_lower)


class CreatePaymentOrderAddonsOnlyLabelTests(TestCase):
    """Spec: '...an add-on charge is never mislabeled as "consolidation" (e.g.
    for a Premium shipment where consolidation_fee is waived to 0 but an
    add-on was purchased).'"""

    def setUp(self):
        self.user = User.objects.create(email='addon-only-label@example.com', is_active=True)
        self.locker = Locker.objects.create(user=self.user, plan_type='paid')  # Premium -> consolidation waived
        self.shipment = make_shipment(
            self.user,
            shipping_cost=Decimal('800.00'),
            consolidation_fee=Decimal('0.00'),
        )
        ShipmentAddon.objects.create(shipment=self.shipment, code='insurance', amount=Decimal('150.00'))
        self.client.force_login(self.user)
        self.url = reverse('payments:create_order', kwargs={'shipment_pk': self.shipment.pk})

    def test_addon_charge_not_labeled_as_consolidation(self):
        p1, p2, p3 = _enable_razorpay('order_premium_addon')
        with p1, p2, p3:
            self.client.post(self.url)

        payment = Payment.objects.get(shipment=self.shipment)
        notes = json.loads(payment.notes)
        self.assertEqual(notes['consolidation_due'], '0.00')
        self.assertEqual(notes['addons_due'], '150.00')
        self.assertNotIn('consolidation', payment.description.lower())
        self.assertIn('add-ons', payment.description.lower())


# ---------------------------------------------------------------------------
# Paid shipment: add-ons never re-charged by a later order
# ---------------------------------------------------------------------------

class CreatePaymentOrderPaidShipmentNoDoubleChargeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='addon-no-double-charge@example.com', is_active=True)
        self.locker = Locker.objects.create(user=self.user, plan_type='free')
        self.shipment = make_shipment(
            self.user,
            shipping_cost=Decimal('800.00'),
            consolidation_fee=Decimal('300.00'),
            payment_status='paid',
        )
        ShipmentAddon.objects.create(shipment=self.shipment, code='gift_wrapping', amount=Decimal('99.00'))
        # New pending storage charge accruing after the shipment was already paid.
        make_batch_charge(self.locker, Decimal('50.00'))
        self.client.force_login(self.user)
        self.url = reverse('payments:create_order', kwargs={'shipment_pk': self.shipment.pk})

    def test_only_pending_storage_is_charged_addons_and_consolidation_are_zeroed(self):
        summary = _payment_summary(self.shipment)

        p1, p2, p3 = _enable_razorpay('order_no_double_charge')
        with p1, p2, p3:
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['amount'], 5000)  # only the 50.00 storage charge
        payment = Payment.objects.get(shipment=self.shipment)
        notes = json.loads(payment.notes)
        self.assertEqual(notes['addons_due'], '0.00')
        self.assertEqual(notes['consolidation_due'], '0.00')
        self.assertEqual(notes['shipping_due'], '0.00')

        # Pin the equivalence property for the *paid* state too, per spec:
        # displayed and actually-charged totals must match in every state.
        charged_total = Decimal(response.json()['amount']) / 100
        self.assertEqual(summary['shipment_amount_due'], charged_total)


# ---------------------------------------------------------------------------
# Displayed total (_payment_summary) vs actually-charged total (CreatePaymentOrderView)
# ---------------------------------------------------------------------------

class DisplayedVsChargedTotalEquivalenceTests(TestCase):
    """Spec: 'The displayed total and the actually-charged total must always
    be the same number for any given shipment state -- this is the property
    a dedicated regression test ... verifies end-to-end (calling
    _payment_summary() and POSTing to the real payments:create_order
    endpoint for the same shipment, with shipping + consolidation + add-ons
    + pending storage all non-zero simultaneously, and asserting the two
    numbers match).'"""

    def setUp(self):
        self.user = User.objects.create(email='displayed-vs-charged@example.com', is_active=True)
        self.locker = Locker.objects.create(user=self.user, plan_type='free')
        self.shipment = make_shipment(
            self.user,
            shipping_cost=Decimal('1234.56'),
            consolidation_fee=Decimal('300.00'),
        )
        ShipmentAddon.objects.create(shipment=self.shipment, code='insurance', amount=Decimal('150.00'))
        ShipmentAddon.objects.create(shipment=self.shipment, code='extra_photos', amount=Decimal('149.00'))
        make_batch_charge(self.locker, Decimal('75.00'))
        self.client.force_login(self.user)
        self.url = reverse('payments:create_order', kwargs={'shipment_pk': self.shipment.pk})

    def test_displayed_amount_due_equals_actually_charged_total(self):
        summary = _payment_summary(self.shipment)
        displayed_total = summary['shipment_amount_due']
        # Sanity: every fee type is non-zero, per the spec's test scenario.
        self.assertGreater(summary['shipping_amount'], 0)
        self.assertGreater(summary['consolidation_fee'], 0)
        self.assertGreater(summary['addons_amount'], 0)
        self.assertGreater(summary['storage_fee_pending'], 0)

        p1, p2, p3 = _enable_razorpay('order_equivalence')
        with p1, p2, p3:
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        charged_total = Decimal(response.json()['amount']) / 100
        self.assertEqual(displayed_total, charged_total)


# ---------------------------------------------------------------------------
# GST invoice: addons_amount snapshot + taxable_amount inclusion
# ---------------------------------------------------------------------------

class InvoiceAddonsAmountTests(TestCase):
    def setUp(self):
        from apps.notifications.models import AppSettings

        self.user = User.objects.create(email='invoice-addons@example.com', is_active=True)
        self.locker = Locker.objects.create(user=self.user, plan_type='free')

        settings = AppSettings.get_settings()
        settings.company_legal_name = 'CamelTrunk Logistics Pvt Ltd'
        settings.company_gstin = '36AAAAA0000A1Z5'
        settings.company_state = 'Telangana'
        settings.gst_rate_percent = Decimal('18.00')
        settings.save()

        self.shipment = make_shipment(
            self.user,
            shipment_type='domestic',
            state='Maharashtra',  # different from company_state -> IGST path
            shipping_cost=Decimal('1000.00'),
            consolidation_fee=Decimal('0.00'),
        )
        ShipmentAddon.objects.create(shipment=self.shipment, code='priority_packing', amount=Decimal('299.00'))

        Payment.objects.create(
            user=self.user, shipment=self.shipment, amount=Decimal('1299.00'),
            payment_method='razorpay', status='captured',
            razorpay_payment_id='pay_addon_invoice', paid_at=timezone.now(),
        )
        Shipment.objects.filter(pk=self.shipment.pk).update(payment_status='paid')
        self.shipment.refresh_from_db()

        self.upload_patcher = patch(
            'apps.payments.services.InvoiceService.upload_pdf',
            return_value='shipment/RB-00001/invoice_fake.pdf',
        )
        self.upload_patcher.start()
        self.addCleanup(self.upload_patcher.stop)

    def test_invoice_addons_amount_matches_shipment_addons_sum(self):
        invoice = InvoiceService.generate_for_shipment(self.shipment, paid_at=timezone.now())
        self.assertEqual(invoice.addons_amount, Decimal('299.00'))

    def test_taxable_amount_includes_addons_amount(self):
        invoice = InvoiceService.generate_for_shipment(self.shipment, paid_at=timezone.now())
        # 1000 shipping + 0 consolidation + 0 storage + 299 addons = 1299.00
        self.assertEqual(invoice.taxable_amount, Decimal('1299.00'))

    def test_gst_is_computed_on_the_addons_inclusive_taxable_amount(self):
        invoice = InvoiceService.generate_for_shipment(self.shipment, paid_at=timezone.now())
        # domestic, different state -> IGST @ 18% of 1299.00 = 233.82
        self.assertEqual(invoice.igst_amount, Decimal('233.82'))
        self.assertEqual(invoice.total_amount, Decimal('1532.82'))
