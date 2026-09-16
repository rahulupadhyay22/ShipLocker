"""Behavioral baseline for apps/payments/ input validation (VerifyPaymentView
and RazorpayWebhookView) -- part of the repo-wide validation audit. Started
as a read-only baseline; now also the regression suite for the approved
reliability/consistency fixes made across this file's lifetime:

- Fix 1 (VerifyPaymentView.post): a JSON body that parses but isn't an
  object (array/scalar/string) used to crash with an unhandled
  AttributeError ('list'/'int'/'str' object has no attribute 'get'). Now
  rejected cleanly with 400 {'error': 'Malformed request body'} -- a
  message distinct from the shape/length validation failure's 'Invalid
  payment parameters' (see the low-priority Item 1 fix below).
- Item 1 (VerifyPaymentView.post, error-message clarity): the non-dict-body
  case above and the per-field shape/length failure (validate_text_input
  rejecting an over-length/dangerous-pattern field) used to return the
  IDENTICAL 'Invalid payment parameters' text despite being different
  failures. Now distinct: 'Malformed request body' vs 'Invalid payment
  parameters'. 'Missing payment parameters' (all-fields-empty case) was
  already distinct and is unchanged. No payment/order IDs, signatures, or
  internal exception text are exposed in any of the three messages.
- Item 2 (RazorpayWebhookView.post, payment.captured): order_id/payment_id
  from the (already HMAC-verified) payload used to reach a DB lookup
  (order_id) or DB write (payment_id -- Payment.razorpay_payment_id is a
  CharField with no null=True, so a missing/None payment_id would violate
  the column's NOT NULL constraint on .save()) with no shape/length guard.
  Now: order_id must be a non-empty string <=100 chars or the whole
  payment.captured branch is skipped exactly as a missing order_id already
  was (existing `if order_id:` no-op pattern, unchanged); payment_id
  defaults to '' if not a valid non-empty string <=100 chars, matching the
  same "fall back to a safe default rather than crash or block a
  legitimate capture" precedent as Fix 2. Length-only, matching the
  Payment model's own max_length=100 on both fields and
  VerifyPaymentView's existing 100-char shape guard for the same
  identifiers in the checkout flow -- no dangerous-pattern filtering
  introduced for this signature-authenticated payload.
- Fix 2 (RazorpayWebhookView.post, payment.failed): error_description used
  to be written directly into payment.failure_reason (CharField(
  max_length=255), Postgres varchar(255)) with no length check -- Django
  does NOT enforce CharField.max_length at .save() time, only via
  full_clean()/ModelForm, which this path never calls -- so a genuinely
  HMAC-signed payload with error_description over 255 chars crashed with
  psycopg2.errors.StringDataRightTruncation. Now length-checked (>255 chars
  falls back to the same 'Payment failed' default already used for a
  missing error_description) before assignment. Scoped to LENGTH only, not
  content -- a short dangerous-pattern error_description is still accepted
  verbatim, unchanged, exactly as before (see
  test_dangerous_pattern_error_description_currently_accepted_and_stored
  below), per the approved fix's explicit "length <= 255 -> preserve
  existing behavior" constraint. indiabox.validators.validate_text_input
  was deliberately NOT reused here -- it also enforces a dangerous-pattern
  check, which would have silently rejected short-but-suspicious content
  that must stay accepted per the approved scope. A plain length comparison
  against the model field's own max_length is the smallest change that
  satisfies the approved behavior exactly.

No application code was modified to produce the ORIGINAL baseline sections
below; the two fixes above are the only application-code changes made in
this file's lifetime, both explicitly approved before implementation.

Everything below was verified directly against apps/payments/views.py and
apps/payments/services.py (not assumed from any prior audit summary):

- VerifyPaymentView.post: json.loads(request.body) -> extract 3 fields with
  .get(default='') -> `if not all([...])` presence check -> EACH of the
  three fields individually validated via
  validate_text_input(min_length=1, max_length=100) (generic 'Invalid
  payment parameters' on any failure, not per-field) -> get_object_or_404
  lookup scoped to (razorpay_order_id, user=request.user) -- this DB lookup
  happens BEFORE HMAC signature verification, but is safe: it's an
  ownership-scoped equality lookup on an already-length/pattern-validated
  string, not a trust decision -- THEN RazorpayService.verify_payment_signature.
  Only after a valid signature does the payment get captured.
- RazorpayWebhookView.post: signature header extracted -> missing check ->
  webhook secret loaded -> verify_webhook_signature(request.body, ...) is
  called against the RAW, UNPARSED bytes -- json.loads(request.body) only
  happens AFTER signature verification succeeds. This is the correct order:
  authenticity is established before the payload is trusted enough to parse
  and act on.
- Inside the webhook, for payment.captured: order_id/payment_id are read via
  plain dict .get() with NO validate_text_input call at all. order_id is
  only ever used in an ORM equality lookup (Payment.objects...get(
  razorpay_order_id=order_id)) -- a non-matching value just raises
  Payment.DoesNotExist (caught, logged, 200 'ok' returned), no error
  surfaces either way.
- For payment.failed: failure_reason = payment_entity.get('error_description',
  'Payment failed') is written DIRECTLY to payment.failure_reason
  (CharField(max_length=255), see apps/payments/models.py) with NO length
  cap or dangerous-pattern check applied before the .save() call.

Trust model note (per the audit's own framing): everything reaching
RazorpayWebhookView.post's business logic already passed HMAC-SHA256
signature verification against a secret only Razorpay and this server
know -- it is provider-attested, not ordinary user input. A finding here is
a data-integrity/robustness question (could a malformed-but-genuinely-signed
payload crash this endpoint?), not a spoofing/injection vulnerability.
"""
import hashlib
import hmac
import json
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Locker, User
from apps.notifications.models import AppSettings
from apps.payments.models import Payment


def make_user_locker(email='payments-baseline@example.com'):
    user = User.objects.create(email=email, is_active=True)
    locker = Locker.objects.create(user=user)
    return user, locker


def make_pending_payment(user, order_id='order_baseline_1', **extra):
    defaults = dict(
        user=user, amount=100, payment_type='premium_subscription',
        payment_method='razorpay', status='pending', razorpay_order_id=order_id,
    )
    defaults.update(extra)
    return Payment.objects.create(**defaults)


# ============================================================================
# VerifyPaymentView
# ============================================================================

class VerifyPaymentValidInputTests(TestCase):
    def setUp(self):
        self.user, self.locker = make_user_locker()
        self.client.force_login(self.user)
        self.payment = make_pending_payment(self.user)
        self.url = reverse('payments:verify')
        self.body = {
            'razorpay_order_id': 'order_baseline_1',
            'razorpay_payment_id': 'pay_baseline_1',
            'razorpay_signature': 'sig_baseline_1',
        }

    def test_valid_input_with_verified_signature_captures_payment(self):
        with patch('apps.payments.services.RazorpayService.verify_payment_signature', return_value=True):
            response = self.client.post(self.url, data=json.dumps(self.body), content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'captured')
        self.assertEqual(self.payment.razorpay_payment_id, 'pay_baseline_1')

    def test_failed_signature_marks_payment_failed_and_returns_400(self):
        with patch('apps.payments.services.RazorpayService.verify_payment_signature', return_value=False):
            response = self.client.post(self.url, data=json.dumps(self.body), content_type='application/json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'failed')
        self.assertEqual(self.payment.failure_reason, 'Signature verification failed')


class VerifyPaymentMissingInputTests(TestCase):
    def setUp(self):
        self.user, self.locker = make_user_locker('verify-missing@example.com')
        self.client.force_login(self.user)
        self.payment = make_pending_payment(self.user)
        self.url = reverse('payments:verify')

    def _post(self, body):
        return self.client.post(self.url, data=json.dumps(body), content_type='application/json')

    def test_missing_order_id_rejected(self):
        response = self._post({'razorpay_payment_id': 'p', 'razorpay_signature': 's'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Missing payment parameters'})

    def test_missing_payment_id_rejected(self):
        response = self._post({'razorpay_order_id': 'o', 'razorpay_signature': 's'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Missing payment parameters'})

    def test_missing_signature_rejected(self):
        response = self._post({'razorpay_order_id': 'o', 'razorpay_payment_id': 'p'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Missing payment parameters'})

    def test_empty_body_rejected(self):
        response = self._post({})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Missing payment parameters'})

    def test_blank_string_fields_rejected_same_as_missing(self):
        """`.get(key, '')` + `all([...])` treats an empty string the same
        as an absent key -- both are falsy."""
        response = self._post({'razorpay_order_id': '', 'razorpay_payment_id': 'p', 'razorpay_signature': 's'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Missing payment parameters'})


class VerifyPaymentMalformedInputTests(TestCase):
    def setUp(self):
        self.user, self.locker = make_user_locker('verify-malformed@example.com')
        self.client.force_login(self.user)
        self.url = reverse('payments:verify')

    def test_invalid_json_body_rejected(self):
        response = self.client.post(self.url, data='not-json{', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content.decode(), 'Invalid JSON')

    def test_non_dict_json_array_rejected_cleanly(self):
        """Fix 1 regression test: a JSON array is valid JSON but has no
        .get() -- VerifyPaymentView.post now checks isinstance(data, dict)
        before calling .get(), returning a clean 400 instead of raising an
        unhandled AttributeError. Message text intentionally distinct from
        the shape/length validation failure below (Item 1 error-message
        clarity fix) -- 'Malformed request body' names this specific
        situation (the body isn't even the right shape to contain
        parameters) rather than reusing 'Invalid payment parameters'."""
        response = self.client.post(self.url, data='[1, 2, 3]', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Malformed request body'})

    def test_non_dict_json_scalar_rejected_cleanly(self):
        """Same fix, a bare JSON scalar (number) instead of an array --
        also not a dict, also has no .get()."""
        response = self.client.post(self.url, data='42', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Malformed request body'})

    def test_non_dict_json_string_rejected_cleanly(self):
        response = self.client.post(self.url, data='"just a string"', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Malformed request body'})


class VerifyPaymentFormatAndBoundaryTests(TestCase):
    def setUp(self):
        self.user, self.locker = make_user_locker('verify-format@example.com')
        self.client.force_login(self.user)
        self.payment = make_pending_payment(self.user, order_id='A' * 100)
        self.url = reverse('payments:verify')

    def _post(self, **overrides):
        body = {
            'razorpay_order_id': 'A' * 100, 'razorpay_payment_id': 'pay_x', 'razorpay_signature': 'sig_x',
        }
        body.update(overrides)
        return self.client.post(self.url, data=json.dumps(body), content_type='application/json')

    def test_order_id_at_100_chars_accepted_by_shape_guard(self):
        with patch('apps.payments.services.RazorpayService.verify_payment_signature', return_value=True):
            response = self._post()
        self.assertEqual(response.status_code, 200)

    def test_order_id_over_100_chars_rejected(self):
        response = self._post(razorpay_order_id='A' * 101)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Invalid payment parameters'})

    def test_dangerous_pattern_in_payment_id_rejected(self):
        response = self._post(razorpay_payment_id='<script>alert(1)</script>')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Invalid payment parameters'})

    def test_dangerous_pattern_in_signature_rejected(self):
        response = self._post(razorpay_signature='javascript:alert(1)')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Invalid payment parameters'})

    def test_rejection_message_is_generic_not_field_specific(self):
        """Pinning: unlike Locker/Shipment/Accounts, the error message here
        does NOT name which of the 3 fields failed -- 'Invalid payment
        parameters' regardless of which one."""
        response = self._post(razorpay_payment_id='<iframe></iframe>')
        self.assertEqual(response.json()['error'], 'Invalid payment parameters')


class VerifyPaymentAuthorizationTests(TestCase):
    def setUp(self):
        self.owner, self.owner_locker = make_user_locker('verify-owner@example.com')
        self.payment = make_pending_payment(self.owner, order_id='order_owner_1')
        self.url = reverse('payments:verify')
        self.body = {
            'razorpay_order_id': 'order_owner_1', 'razorpay_payment_id': 'p', 'razorpay_signature': 's',
        }

    def test_anonymous_post_redirects_to_login(self):
        response = self.client.post(self.url, data=json.dumps(self.body), content_type='application/json')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_another_users_payment_404s_not_leaked(self):
        """Ownership-scoped get_object_or_404(razorpay_order_id=..., user=
        request.user) -- a different logged-in user posting the SAME
        order_id gets a plain 404, not the owner's payment."""
        other_user, other_locker = make_user_locker('verify-other@example.com')
        self.client.force_login(other_user)
        response = self.client.post(self.url, data=json.dumps(self.body), content_type='application/json')
        self.assertEqual(response.status_code, 404)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'pending')


# ============================================================================
# RazorpayWebhookView
# ============================================================================

class WebhookSignatureTests(TestCase):
    def setUp(self):
        settings = AppSettings.get_settings()
        settings.razorpay_webhook_secret = 'test_webhook_secret'
        settings.save()
        self.url = reverse('payments:razorpay_webhook')
        self.payload = json.dumps({
            'event': 'payment.captured',
            'payload': {'payment': {'entity': {'order_id': 'order_wh_1', 'id': 'pay_wh_1'}}},
        }).encode('utf-8')

    def _valid_signature(self, body=None):
        return hmac.new(b'test_webhook_secret', body or self.payload, hashlib.sha256).hexdigest()

    def test_valid_signature_accepted(self):
        response = self.client.post(
            self.url, data=self.payload, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=self._valid_signature(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_missing_signature_header_rejected(self):
        response = self.client.post(self.url, data=self.payload, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content.decode(), 'Missing signature')

    def test_invalid_signature_rejected(self):
        response = self.client.post(
            self.url, data=self.payload, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE='0' * 64,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content.decode(), 'Invalid signature')

    def test_signature_computed_over_modified_body_rejected(self):
        """Confirms the signature is checked against the EXACT bytes
        received -- a signature valid for one payload must not validate a
        different (even single-byte-different) payload."""
        tampered = self.payload.replace(b'order_wh_1', b'order_wh_2')
        response = self.client.post(
            self.url, data=tampered, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=self._valid_signature(),  # signed for the ORIGINAL payload
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content.decode(), 'Invalid signature')

    def test_webhook_secret_not_configured_returns_503(self):
        settings = AppSettings.get_settings()
        settings.razorpay_webhook_secret = ''
        settings.save()
        response = self.client.post(
            self.url, data=self.payload, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=self._valid_signature(),
        )
        self.assertEqual(response.status_code, 503)


class WebhookMalformedPayloadTests(TestCase):
    def setUp(self):
        settings = AppSettings.get_settings()
        settings.razorpay_webhook_secret = 'test_webhook_secret'
        settings.save()
        self.url = reverse('payments:razorpay_webhook')

    def _post(self, body_bytes):
        signature = hmac.new(b'test_webhook_secret', body_bytes, hashlib.sha256).hexdigest()
        return self.client.post(
            self.url, data=body_bytes, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=signature,
        )

    def test_invalid_json_after_valid_signature_rejected(self):
        """The signature can validate any byte string, including one that
        isn't valid JSON -- json.loads() runs AFTER signature verification,
        so this specific rejection happens post-authentication."""
        response = self._post(b'not-json{')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content.decode(), 'Invalid JSON')

    def test_unknown_event_type_returns_ok_noop(self):
        payload = json.dumps({'event': 'refund.created', 'payload': {}}).encode('utf-8')
        response = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_missing_event_key_returns_ok_noop(self):
        payload = json.dumps({'payload': {}}).encode('utf-8')
        response = self._post(payload)
        self.assertEqual(response.status_code, 200)

    def test_payment_captured_missing_order_id_returns_ok_noop(self):
        """order_id absent from the payment entity -- the `if order_id:`
        guard silently skips, no error, no crash."""
        payload = json.dumps({
            'event': 'payment.captured',
            'payload': {'payment': {'entity': {'id': 'pay_no_order'}}},
        }).encode('utf-8')
        response = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_payment_captured_with_extra_unknown_fields_ignored(self):
        payload = json.dumps({
            'event': 'payment.captured',
            'payload': {'payment': {'entity': {
                'order_id': 'order_extra_1', 'id': 'pay_extra_1',
                'unexpected_field': 'whatever', 'method': 'card', 'vpa': 'x@y',
            }}},
            'unexpected_top_level_key': True,
        }).encode('utf-8')
        response = self._post(payload)
        self.assertEqual(response.status_code, 200)


class WebhookOrderIdValidationGapTests(TestCase):
    """Item 2 fix: order_id/payment_id in the payment.captured webhook
    payload now go through a shape/length guard (non-empty string, <=100
    chars, matching the Payment model's own max_length=100 on both fields)
    before order_id reaches the ORM lookup or payment_id reaches the
    razorpay_payment_id assignment. No dangerous-pattern filtering was
    added -- this payload is already HMAC-authenticated, not
    attacker-controlled free text (see the module docstring for the full
    rationale). HMAC verification-before-parsing is unaffected -- see
    WebhookSignatureTests, which covers that ordering independently."""

    def setUp(self):
        settings = AppSettings.get_settings()
        settings.razorpay_webhook_secret = 'test_webhook_secret'
        settings.save()
        self.url = reverse('payments:razorpay_webhook')

    def _post(self, order_id, payment_id='pay_x'):
        payload = json.dumps({
            'event': 'payment.captured',
            'payload': {'payment': {'entity': {'order_id': order_id, 'id': payment_id}}},
        }).encode('utf-8')
        signature = hmac.new(b'test_webhook_secret', payload, hashlib.sha256).hexdigest()
        return self.client.post(
            self.url, data=payload, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=signature,
        )

    def test_nonexistent_order_id_returns_ok_noop_no_error(self):
        response = self._post('order_does_not_exist')
        self.assertEqual(response.status_code, 200)

    def test_dangerous_pattern_order_id_still_not_content_filtered(self):
        """A script-tag-shaped order_id is well within the 100-char shape
        guard (length/type only, no content filtering by design for this
        signature-authenticated payload) -- it just fails to match any
        Payment row (no legitimate order_id looks like this) and is
        silently ignored, exactly as before the Item 2 fix."""
        response = self._post('<script>alert(1)</script>')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_valid_order_id_and_payment_id_still_capture_payment(self):
        """Requirement: valid IDs still work, unaffected by the new guard."""
        user, _ = make_user_locker('webhook-orderid-valid@example.com')
        payment = make_pending_payment(user, order_id='order_valid_1')
        response = self._post('order_valid_1', payment_id='pay_valid_1')
        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'captured')
        self.assertEqual(payment.razorpay_payment_id, 'pay_valid_1')

    def test_order_id_over_100_chars_now_skipped_by_shape_guard(self):
        """Fixed behavior: an order_id over the 100-char guard is now
        treated the same as a missing order_id (skip, no lookup attempted)
        -- externally indistinguishable from the pre-fix behavior (a
        >100-char string could never match any real Payment row's
        max_length=100 razorpay_order_id anyway, so both paths always
        resulted in a no-op), but now short-circuited defensively instead
        of relying on the ORM miss."""
        response = self._post('A' * 5000)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_order_id_at_exactly_100_chars_still_works(self):
        user, _ = make_user_locker('webhook-orderid-boundary@example.com')
        order_id = 'B' * 100
        payment = make_pending_payment(user, order_id=order_id)
        response = self._post(order_id, payment_id='pay_boundary_1')
        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'captured')

    def test_empty_order_id_rejected_same_as_missing(self):
        response = self._post('')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_non_string_order_id_rejected_safely_no_crash(self):
        """Malformed type (a dict instead of a string) -- previously this
        would have reached Payment.objects.get(razorpay_order_id={...}),
        risking an ORM/adapter TypeError. Now safely skipped, no crash,
        matching the missing-order_id no-op pattern."""
        payload = json.dumps({
            'event': 'payment.captured',
            'payload': {'payment': {'entity': {'order_id': {'nested': 'value'}, 'id': 'pay_x'}}},
        }).encode('utf-8')
        signature = hmac.new(b'test_webhook_secret', payload, hashlib.sha256).hexdigest()
        response = self.client.post(
            self.url, data=payload, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_missing_payment_id_falls_back_to_empty_string_no_crash(self):
        """payment_id absent from the entity -- previously would have
        assigned None to razorpay_payment_id (a NOT NULL CharField),
        risking an IntegrityError on .save(). Now falls back to '' and the
        capture still succeeds."""
        user, _ = make_user_locker('webhook-paymentid-missing@example.com')
        payment = make_pending_payment(user, order_id='order_no_payid_1')
        payload = json.dumps({
            'event': 'payment.captured',
            'payload': {'payment': {'entity': {'order_id': 'order_no_payid_1'}}},
        }).encode('utf-8')
        signature = hmac.new(b'test_webhook_secret', payload, hashlib.sha256).hexdigest()
        response = self.client.post(
            self.url, data=payload, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'captured')
        self.assertEqual(payment.razorpay_payment_id, '')

    def test_oversized_payment_id_falls_back_to_empty_string_no_crash(self):
        """payment_id over 100 chars -- previously would have crashed on
        .save() the same class of bug fixed for error_description above.
        Now falls back to '' and the capture still succeeds."""
        user, _ = make_user_locker('webhook-paymentid-oversized@example.com')
        payment = make_pending_payment(user, order_id='order_oversized_payid_1')
        response = self._post('order_oversized_payid_1', payment_id='P' * 200)
        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'captured')
        self.assertEqual(payment.razorpay_payment_id, '')


class WebhookErrorDescriptionValidationGapTests(TestCase):
    """Pinning the confirmed gap for payment.failed: error_description is
    written directly to payment.failure_reason (CharField(max_length=255))
    with no validate_text_input call -- no length cap, no dangerous-pattern
    check, applied before .save()."""

    def setUp(self):
        settings = AppSettings.get_settings()
        settings.razorpay_webhook_secret = 'test_webhook_secret'
        settings.save()
        self.url = reverse('payments:razorpay_webhook')
        self.user, _ = make_user_locker('webhook-faildesc@example.com')

    def _post_failed(self, order_id, error_description):
        payload = json.dumps({
            'event': 'payment.failed',
            'payload': {'payment': {'entity': {'order_id': order_id, 'error_description': error_description}}},
        }).encode('utf-8')
        signature = hmac.new(b'test_webhook_secret', payload, hashlib.sha256).hexdigest()
        return self.client.post(
            self.url, data=payload, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=signature,
        )

    def test_normal_error_description_stored_verbatim(self):
        payment = make_pending_payment(self.user, order_id='order_fail_1')
        response = self._post_failed('order_fail_1', 'Card declined by issuing bank')
        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'failed')
        self.assertEqual(payment.failure_reason, 'Card declined by issuing bank')

    def test_dangerous_pattern_error_description_currently_accepted_and_stored(self):
        payment = make_pending_payment(self.user, order_id='order_fail_2')
        response = self._post_failed('order_fail_2', '<script>alert(1)</script>')
        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.failure_reason, '<script>alert(1)</script>')

    def test_error_description_at_255_chars_stored(self):
        payment = make_pending_payment(self.user, order_id='order_fail_3')
        text = 'A' * 255
        response = self._post_failed('order_fail_3', text)
        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.failure_reason, text)

    def test_error_description_at_exactly_255_chars_accepted(self):
        """Boundary: exactly 255 chars is the max allowed, not the first
        rejected value -- must be stored verbatim, not treated as the
        overflow case."""
        payment = make_pending_payment(self.user, order_id='order_fail_255')
        text = 'A' * 255
        response = self._post_failed('order_fail_255', text)
        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'failed')
        self.assertEqual(payment.failure_reason, text)

    def test_error_description_over_255_chars_no_longer_crashes(self):
        """Fix 2 regression test: previously CONFIRMED to crash with
        psycopg2.errors.StringDataRightTruncation (payment.save() writes
        error_description directly into failure_reason, a
        CharField(max_length=255) that Django does NOT enforce at .save()
        time -- only full_clean()/ModelForm validation does, and this path
        never called either). RazorpayWebhookView now reuses
        indiabox.validators.validate_text_input(max_length=255,
        required=False) before assignment; on rejection it falls back to
        the same 'Payment failed' default text already used when
        error_description is absent entirely -- not a new convention, and
        not a truncation of the oversized value. The webhook still returns
        200 'ok' (the existing error-handling pattern: log and continue,
        never surface a raw exception to Razorpay), and the payment is
        still correctly marked failed."""
        payment = make_pending_payment(self.user, order_id='order_fail_4')
        text = 'A' * 256
        response = self._post_failed('order_fail_4', text)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'failed')
        self.assertEqual(payment.failure_reason, 'Payment failed')
        self.assertLessEqual(len(payment.failure_reason), 255)

    def test_error_description_way_over_255_chars_no_longer_crashes(self):
        payment = make_pending_payment(self.user, order_id='order_fail_5')
        response = self._post_failed('order_fail_5', 'A' * 5000)
        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'failed')
        self.assertEqual(payment.failure_reason, 'Payment failed')
