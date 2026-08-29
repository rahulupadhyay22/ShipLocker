import json
from decimal import Decimal
from unittest.mock import patch, PropertyMock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User, Locker
from apps.locker.models import Parcel, ReturnRequest, DiscardRequest
from apps.locker.services.returns import finalize_return_request
from apps.payments.models import Payment


def _enable_razorpay(order_id='order_return_test'):
    """Same shape as apps/payments/tests.py's _enable_razorpay helper."""
    return (
        patch('apps.payments.services.RazorpayService.is_enabled', new_callable=PropertyMock, return_value=True),
        patch('apps.payments.services.RazorpayService.key_id', new_callable=PropertyMock, return_value='rzp_test_key'),
        patch('apps.payments.services.RazorpayService.create_order', return_value={'id': order_id}),
    )


class CreateReturnPaymentOrderViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='return-checkout@example.com', is_active=True)
        self.locker = Locker.objects.create(user=self.user)
        self.parcel = Parcel.objects.create(locker=self.locker, item_name='Shoes', status='approved')
        # return_service_charge (₹199 flat) is seeded by
        # apps/content/migrations/0012_seed_return_service_charge.py.
        self.client.force_login(self.user)
        self.url = reverse('locker:request_return', kwargs={'pk': self.parcel.pk})

    def test_parcel_detail_page_renders_with_return_form(self):
        """Nothing else exercises this template render — a syntax error in
        the new checkout script would 500 this page silently otherwise."""
        response = self.client.get(reverse('locker:parcel_detail', kwargs={'pk': self.parcel.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'returnForm')

    def test_creates_pending_payment_and_order(self):
        p1, p2, p3 = _enable_razorpay('order_return_1')
        with p1, p2, p3:
            response = self.client.post(self.url, {'reason': 'Wrong size'})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['order_id'], 'order_return_1')
        self.assertEqual(data['amount'], 19900)

        payment = Payment.objects.get(user=self.user, payment_type='return_service_charge')
        self.assertEqual(payment.status, 'pending')
        self.assertEqual(payment.amount, Decimal('199.00'))
        notes = json.loads(payment.notes)
        self.assertEqual(notes['parcel_id'], str(self.parcel.pk))
        self.assertEqual(notes['reason'], 'Wrong size')

        # Parcel/ReturnRequest are untouched until the payment is captured.
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'approved')
        self.assertFalse(ReturnRequest.objects.filter(parcel=self.parcel).exists())

    def test_duplicate_post_within_window_returns_same_order(self):
        p1, p2, p3 = _enable_razorpay('order_return_2')
        with p1, p2, p3:
            first = self.client.post(self.url, {'reason': 'Wrong size'})
            second = self.client.post(self.url, {'reason': 'Wrong size'})

        self.assertEqual(first.json()['order_id'], second.json()['order_id'])
        self.assertEqual(
            Payment.objects.filter(user=self.user, payment_type='return_service_charge').count(), 1
        )

    def test_rejects_ineligible_parcel_status(self):
        self.parcel.status = 'shipped'
        self.parcel.save()

        p1, p2, p3 = _enable_razorpay()
        with p1, p2, p3:
            response = self.client.post(self.url, {'reason': 'Wrong size'})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Payment.objects.filter(payment_type='return_service_charge').exists())

    def test_missing_reason_rejected(self):
        response = self.client.post(self.url, {'reason': ''})
        self.assertEqual(response.status_code, 400)

    def test_inactive_charge_creates_return_request_directly_without_payment(self):
        from apps.content.models import ServiceCharge
        charge = ServiceCharge.objects.get(code='return_service_charge')
        charge.is_active = False
        charge.save()  # .save() (not .update()) so the post_save cache-invalidation signal fires

        response = self.client.post(self.url, {'reason': 'Wrong size'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'success'})
        self.assertFalse(Payment.objects.filter(payment_type='return_service_charge').exists())
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'return_requested')
        self.assertTrue(ReturnRequest.objects.filter(parcel=self.parcel, reason='Wrong size').exists())


class FinalizeReturnRequestTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='return-finalize@example.com', is_active=True)
        self.locker = Locker.objects.create(user=self.user)
        self.parcel = Parcel.objects.create(locker=self.locker, item_name='Shoes', status='approved')

    def _payment(self, parcel_id, reason='Wrong size'):
        return Payment.objects.create(
            user=self.user, amount=Decimal('199.00'), payment_type='return_service_charge',
            payment_method='razorpay', status='captured', paid_at=timezone.now(),
            notes=json.dumps({'parcel_id': parcel_id, 'reason': reason}),
        )

    def test_creates_return_request_and_updates_parcel_status(self):
        finalize_return_request(self._payment(str(self.parcel.pk)))

        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'return_requested')
        return_request = ReturnRequest.objects.get(parcel=self.parcel)
        self.assertEqual(return_request.reason, 'Wrong size')

    def test_skips_if_parcel_no_longer_eligible(self):
        self.parcel.status = 'shipped'
        self.parcel.save()

        finalize_return_request(self._payment(str(self.parcel.pk)))

        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'shipped')
        self.assertFalse(ReturnRequest.objects.filter(parcel=self.parcel).exists())

    def test_skips_if_parcel_not_found(self):
        # Should not raise even with a bogus/missing parcel_id.
        finalize_return_request(self._payment('00000000-0000-0000-0000-000000000000'))
        self.assertFalse(ReturnRequest.objects.exists())


class VerifyPaymentViewReturnChargeTests(TestCase):
    """End-to-end: VerifyPaymentView capturing a return_service_charge
    payment must create the ReturnRequest via apply_payment_captured_side_effects."""

    def setUp(self):
        self.user = User.objects.create(email='return-verify@example.com', is_active=True)
        self.locker = Locker.objects.create(user=self.user)
        self.parcel = Parcel.objects.create(locker=self.locker, item_name='Shoes', status='approved')
        self.client.force_login(self.user)
        self.payment = Payment.objects.create(
            user=self.user, amount=Decimal('199.00'), payment_type='return_service_charge',
            payment_method='razorpay', status='pending', razorpay_order_id='order_return_verify_1',
            notes=json.dumps({'parcel_id': str(self.parcel.pk), 'reason': 'Wrong size'}),
        )
        self.url = reverse('payments:verify')
        self.body = {
            'razorpay_order_id': 'order_return_verify_1',
            'razorpay_payment_id': 'pay_return_verify_1',
            'razorpay_signature': 'sig_return_verify_1',
        }

    def test_capture_creates_return_request(self):
        with patch('apps.payments.services.RazorpayService.verify_payment_signature', return_value=True):
            response = self.client.post(self.url, data=json.dumps(self.body), content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'return_requested')
        self.assertTrue(ReturnRequest.objects.filter(parcel=self.parcel, reason='Wrong size').exists())


class ParcelDetailTimelineTests(TestCase):
    """The timeline must branch to the return/discard sub-steps instead of
    showing the normal shipping steps once a parcel is in that flow."""

    def setUp(self):
        self.user = User.objects.create(email='return-timeline@example.com', is_active=True)
        self.locker = Locker.objects.create(user=self.user)
        self.client.force_login(self.user)

    def _get(self, parcel):
        return self.client.get(reverse('locker:parcel_detail', kwargs={'pk': parcel.pk}))

    def test_normal_parcel_shows_shipped_step(self):
        parcel = Parcel.objects.create(locker=self.locker, item_name='Shoes', status='approved')
        response = self._get(parcel)
        self.assertContains(response, 'Shipped to You')
        self.assertNotContains(response, 'Return Requested')

    def test_return_requested_shows_return_steps_not_shipped(self):
        parcel = Parcel.objects.create(locker=self.locker, item_name='Shoes', status='return_requested')
        ReturnRequest.objects.create(parcel=parcel, reason='Wrong size')
        response = self._get(parcel)
        self.assertContains(response, 'Return Requested')
        self.assertContains(response, 'Return Approved')
        self.assertContains(response, 'Returned')
        self.assertNotContains(response, 'Shipped to You')

    def test_rejected_return_shows_rejected_step_not_approved(self):
        parcel = Parcel.objects.create(locker=self.locker, item_name='Shoes', status='return_requested')
        ReturnRequest.objects.create(parcel=parcel, reason='Wrong size', status='rejected')
        response = self._get(parcel)
        self.assertContains(response, 'Return Rejected')
        self.assertNotContains(response, 'Return Approved')

    def test_discard_requested_shows_discard_steps_not_shipped(self):
        parcel = Parcel.objects.create(locker=self.locker, item_name='Shoes', status='discard_requested')
        DiscardRequest.objects.create(parcel=parcel, reason='Not wanted')
        response = self._get(parcel)
        self.assertContains(response, 'Discard Requested')
        self.assertContains(response, 'Discarded')
        self.assertNotContains(response, 'Shipped to You')

    def test_approved_parcel_after_past_rejected_return_still_shows_shipped_step(self):
        """Regression guard: a parcel back to 'approved' after a past
        rejected return must not be misrouted into the return branch just
        because 'approved' is a substring of 'return_approved'."""
        parcel = Parcel.objects.create(locker=self.locker, item_name='Shoes', status='approved', approved_at=timezone.now())
        ReturnRequest.objects.create(parcel=parcel, reason='Changed my mind', status='rejected')
        response = self._get(parcel)
        self.assertContains(response, 'Shipped to You')
        self.assertNotContains(response, 'Return Requested')
