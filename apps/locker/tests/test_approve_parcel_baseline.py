"""Baseline/regression tests for ApproveParcelView.post's field validation,
captured BEFORE migrating apps/locker/views.py to Django Forms (see the
Locker validation audit). These pin the exact current manual-validation
behavior -- including error message text -- so the upcoming ParcelApprovalForm
migration can be verified against them without changing what the endpoint
actually does.

None of this touches apps/locker/views.py, forms.py (doesn't exist yet), or
indiabox/validators.py -- test-only, per the approved audit sequencing.
"""
from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User, Locker
from apps.locker.models import Parcel


def make_user_locker(email='approver@example.com'):
    user = User.objects.create(email=email, is_active=True)
    locker = Locker.objects.create(user=user)
    return user, locker


def make_action_required_parcel(locker, **extra):
    defaults = dict(locker=locker, status='action_required', item_name='', weight_kg=Decimal('1.0'))
    defaults.update(extra)
    return Parcel.objects.create(**defaults)


class ApproveParcelValidFieldsTests(TestCase):
    """Requirement 1: a fully valid approval succeeds and persists every field."""

    def setUp(self):
        self.user, self.locker = make_user_locker()
        self.parcel = make_action_required_parcel(self.locker)
        self.client.force_login(self.user)
        self.url = reverse('locker:approve_parcel', kwargs={'pk': self.parcel.pk})

    def test_valid_submission_approves_and_saves_all_fields(self):
        response = self.client.post(self.url, {
            'item_name': 'Running Shoes',
            'item_price': '49.99',
            'category': 'sports',
            'customs_description': 'A pair of running shoes',
        })

        self.assertRedirects(response, reverse('locker:ready_to_ship'))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'approved')
        self.assertIsNotNone(self.parcel.approved_at)
        self.assertEqual(self.parcel.item_name, 'Running Shoes')
        self.assertEqual(self.parcel.item_price, Decimal('49.99'))
        self.assertEqual(self.parcel.category, 'sports')
        self.assertEqual(self.parcel.customs_description, 'A pair of running shoes')

    def test_blank_optional_fields_accepted(self):
        """item_name/category/customs_description are all optional -- an
        approval with none of them supplied must still succeed."""
        response = self.client.post(self.url, {})

        self.assertRedirects(response, reverse('locker:ready_to_ship'))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'approved')

    def test_non_approvable_status_rejected(self):
        self.parcel.status = 'approved'
        self.parcel.save()

        response = self.client.post(self.url, {'item_name': 'X'})

        self.assertRedirects(response, reverse('locker:parcel_detail', kwargs={'pk': self.parcel.pk}))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'approved')  # unchanged, not re-approved


class ApproveParcelItemPriceTests(TestCase):
    """Requirement 2: invalid item_price is rejected, parcel stays action_required."""

    def setUp(self):
        self.user, self.locker = make_user_locker('price-tester@example.com')
        self.parcel = make_action_required_parcel(self.locker)
        self.client.force_login(self.user)
        self.url = reverse('locker:approve_parcel', kwargs={'pk': self.parcel.pk})

    def _assert_rejected(self, item_price):
        response = self.client.post(self.url, {'item_price': item_price})
        self.assertRedirects(response, reverse('locker:parcel_detail', kwargs={'pk': self.parcel.pk}))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'action_required')
        self.assertIsNone(self.parcel.item_price)

    def test_non_numeric_price_rejected(self):
        self._assert_rejected('not-a-number')

    def test_negative_price_rejected(self):
        self._assert_rejected('-10.00')

    def test_nan_price_rejected(self):
        self._assert_rejected('NaN')

    def test_infinity_price_rejected(self):
        self._assert_rejected('Infinity')

    def test_too_many_decimal_places_rejected(self):
        self._assert_rejected('10.999')

    def test_exceeds_max_digits_rejected(self):
        self._assert_rejected('99999999999.99')

    def test_valid_price_accepted(self):
        response = self.client.post(self.url, {'item_price': '25.50'})
        self.assertRedirects(response, reverse('locker:ready_to_ship'))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.item_price, Decimal('25.50'))


class ApproveParcelCategoryTests(TestCase):
    """Requirements 3 and 8: invalid category rejected with the exact
    existing 'Invalid category.' message, and every real choice accepted."""

    def setUp(self):
        self.user, self.locker = make_user_locker('category-tester@example.com')
        self.parcel = make_action_required_parcel(self.locker)
        self.client.force_login(self.user)
        self.url = reverse('locker:approve_parcel', kwargs={'pk': self.parcel.pk})

    def test_invalid_category_rejected_with_exact_message(self):
        """Pins the CURRENT exact flashed text, not the intended one: the
        view does `messages.error(request, str(e))` on a bare
        ValidationError('Invalid category.'), and Django's
        ValidationError.__str__() renders that as a repr'd single-item list
        -- "['Invalid category.']" -- not the plain string. Preserving this
        exactly (warts included) is what requirement 8 asks for; a future
        ParcelApprovalForm using form.errors instead of str(exception) would
        naturally drop the brackets, which is a deliberate, separately
        call-out-able behavior change at migration time, not something to
        silently fix here."""
        response = self.client.post(self.url, {'category': 'not_a_real_category'}, follow=True)

        messages = [str(m) for m in response.context['messages']]
        self.assertIn("['Invalid category.']", messages)
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'action_required')
        self.assertEqual(self.parcel.category, '')

    def test_blank_category_accepted(self):
        response = self.client.post(self.url, {'category': ''})
        self.assertRedirects(response, reverse('locker:ready_to_ship'))

    def test_all_valid_category_choices_accepted(self):
        for code, _label in Parcel.CATEGORY_CHOICES:
            with self.subTest(category=code):
                parcel = make_action_required_parcel(self.locker)
                url = reverse('locker:approve_parcel', kwargs={'pk': parcel.pk})
                response = self.client.post(url, {'category': code})
                self.assertRedirects(response, reverse('locker:ready_to_ship'))
                parcel.refresh_from_db()
                self.assertEqual(parcel.category, code)


class ApproveParcelTextFieldsTests(TestCase):
    """Requirement 4: item_name / customs_description dangerous-input and
    length behavior, matching indiabox.validators.validate_text_input's
    rules exactly (255-char cap for item_name, 2000 for customs_description,
    dangerous-pattern blocklist for both)."""

    def setUp(self):
        self.user, self.locker = make_user_locker('text-tester@example.com')
        self.parcel = make_action_required_parcel(self.locker)
        self.client.force_login(self.user)
        self.url = reverse('locker:approve_parcel', kwargs={'pk': self.parcel.pk})

    def _assert_rejected(self, **fields):
        response = self.client.post(self.url, fields)
        self.assertRedirects(response, reverse('locker:parcel_detail', kwargs={'pk': self.parcel.pk}))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'action_required')

    def test_item_name_with_script_tag_rejected(self):
        self._assert_rejected(item_name='<script>alert(1)</script>')

    def test_item_name_over_255_chars_rejected(self):
        self._assert_rejected(item_name='A' * 256)

    def test_item_name_at_255_chars_accepted(self):
        name = 'A' * 255
        response = self.client.post(self.url, {'item_name': name})
        self.assertRedirects(response, reverse('locker:ready_to_ship'))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.item_name, name)

    def test_customs_description_with_iframe_rejected(self):
        self._assert_rejected(customs_description='<iframe src="evil.com"></iframe>')

    def test_customs_description_over_2000_chars_rejected(self):
        self._assert_rejected(customs_description='A' * 2001)

    def test_customs_description_at_2000_chars_accepted(self):
        desc = 'A' * 2000
        response = self.client.post(self.url, {'customs_description': desc})
        self.assertRedirects(response, reverse('locker:ready_to_ship'))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.customs_description, desc)

    def test_javascript_url_in_item_name_rejected(self):
        self._assert_rejected(item_name='javascript:alert(1)')

    def test_on_event_handler_in_customs_description_rejected(self):
        self._assert_rejected(customs_description='<img onerror=alert(1)>')


class ApproveParcelInvoiceUploadTests(TestCase):
    """Requirement 5: invoice file validation rejects invalid type/extension/
    magic-bytes/size exactly as validate_file_upload currently does."""

    def setUp(self):
        self.user, self.locker = make_user_locker('invoice-tester@example.com')
        self.parcel = make_action_required_parcel(self.locker)
        self.client.force_login(self.user)
        self.url = reverse('locker:approve_parcel', kwargs={'pk': self.parcel.pk})

    def _post_file(self, upload):
        return self.client.post(self.url, {'invoice': upload})

    @patch('apps.accounts.services.SupabaseStorage.upload_file')
    def test_valid_pdf_accepted_and_uploaded(self, mock_upload):
        upload = SimpleUploadedFile('receipt.pdf', b'%PDF-1.4 fake content', content_type='application/pdf')
        response = self._post_file(upload)

        self.assertRedirects(response, reverse('locker:ready_to_ship'))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'approved')
        self.assertTrue(self.parcel.invoice_url)
        mock_upload.assert_called_once()

    def test_disallowed_content_type_rejected(self):
        upload = SimpleUploadedFile('receipt.exe', b'MZfake', content_type='application/x-msdownload')
        response = self._post_file(upload)

        self.assertRedirects(response, reverse('locker:parcel_detail', kwargs={'pk': self.parcel.pk}))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'action_required')
        self.assertEqual(self.parcel.invoice_url, '')

    def test_extension_mismatched_with_content_type_rejected(self):
        """.txt extension with an image/png content-type -- extension must
        match the declared type."""
        upload = SimpleUploadedFile('receipt.txt', b'\x89PNG\r\n\x1a\nfakepng', content_type='image/png')
        response = self._post_file(upload)

        self.assertRedirects(response, reverse('locker:parcel_detail', kwargs={'pk': self.parcel.pk}))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.invoice_url, '')

    def test_magic_bytes_not_matching_declared_type_rejected(self):
        """Correct extension/content-type, but the actual file bytes are not
        a real PDF -- validate_file_upload's magic-byte sniff must catch this."""
        upload = SimpleUploadedFile('receipt.pdf', b'NOT_A_REAL_PDF_HEADER', content_type='application/pdf')
        response = self._post_file(upload)

        self.assertRedirects(response, reverse('locker:parcel_detail', kwargs={'pk': self.parcel.pk}))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.invoice_url, '')

    def test_oversized_file_rejected(self):
        from django.conf import settings
        max_size = getattr(settings, 'MAX_UPLOAD_SIZE', 5 * 1024 * 1024)
        oversized_content = b'%PDF-' + (b'A' * (max_size + 1))
        upload = SimpleUploadedFile('receipt.pdf', oversized_content, content_type='application/pdf')
        response = self._post_file(upload)

        self.assertRedirects(response, reverse('locker:parcel_detail', kwargs={'pk': self.parcel.pk}))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.invoice_url, '')

    def test_no_invoice_file_still_approves(self):
        """invoice is entirely optional -- omitting it must not block approval."""
        response = self.client.post(self.url, {})
        self.assertRedirects(response, reverse('locker:ready_to_ship'))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'approved')
        self.assertEqual(self.parcel.invoice_url, '')

    @patch('apps.locker.utils.upload_invoice', side_effect=Exception('supabase down'))
    def test_upload_failure_still_approves_with_warning(self, mock_upload_invoice):
        """Existing behavior: an upload failure warns but does NOT block
        approval (see the try/except around upload_invoice in the view)."""
        upload = SimpleUploadedFile('receipt.pdf', b'%PDF-1.4 fake content', content_type='application/pdf')
        response = self.client.post(self.url, {'invoice': upload}, follow=True)

        messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('Invoice upload failed' in m for m in messages))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'approved')
        self.assertEqual(self.parcel.invoice_url, '')


class RequestDiscardBaselineTests(TestCase):
    """Requirement 7: RequestDiscardView's optional-reason behavior and
    rejection of invalid input, before the ReasonForm migration."""

    def setUp(self):
        self.user, self.locker = make_user_locker('discard-tester@example.com')
        self.parcel = make_action_required_parcel(self.locker)
        self.client.force_login(self.user)
        self.url = reverse('locker:request_discard', kwargs={'pk': self.parcel.pk})

    def test_blank_reason_accepted(self):
        """reason is optional for discard (required=False), unlike return."""
        response = self.client.post(self.url, {'reason': ''})

        self.assertRedirects(response, reverse('locker:discards'))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'discard_requested')

    def test_missing_reason_key_accepted(self):
        response = self.client.post(self.url, {})

        self.assertRedirects(response, reverse('locker:discards'))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'discard_requested')

    def test_valid_reason_saved(self):
        response = self.client.post(self.url, {'reason': 'No longer needed'})

        self.assertRedirects(response, reverse('locker:discards'))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'discard_requested')
        self.assertEqual(self.parcel.discard_requests.get().reason, 'No longer needed')

    def test_dangerous_reason_rejected(self):
        response = self.client.post(self.url, {'reason': '<script>alert(1)</script>'})

        self.assertRedirects(response, reverse('locker:parcel_detail', kwargs={'pk': self.parcel.pk}))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'action_required')
        self.assertFalse(self.parcel.discard_requests.exists())

    def test_overlong_reason_rejected(self):
        response = self.client.post(self.url, {'reason': 'A' * 501})

        self.assertRedirects(response, reverse('locker:parcel_detail', kwargs={'pk': self.parcel.pk}))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'action_required')

    def test_non_requestable_status_rejected(self):
        self.parcel.status = 'shipped'
        self.parcel.save()

        response = self.client.post(self.url, {'reason': 'test'})

        self.assertRedirects(response, reverse('locker:parcel_detail', kwargs={'pk': self.parcel.pk}))
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'shipped')


class CreateReturnPaymentOrderReasonBaselineTests(TestCase):
    """Requirement 6: CreateReturnPaymentOrderView still rejects missing/
    invalid reason with the existing JSON error response (status=400),
    complementing the existing test_missing_reason_rejected in
    test_return_service_charge.py with dangerous-input and length cases."""

    def setUp(self):
        self.user, self.locker = make_user_locker('return-reason-tester@example.com')
        self.parcel = Parcel.objects.create(locker=self.locker, item_name='Shoes', status='approved')
        self.client.force_login(self.user)
        self.url = reverse('locker:request_return', kwargs={'pk': self.parcel.pk})

    def test_dangerous_reason_rejected_as_json_400(self):
        response = self.client.post(self.url, {'reason': '<script>alert(1)</script>'})

        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())

    def test_overlong_reason_rejected_as_json_400(self):
        response = self.client.post(self.url, {'reason': 'A' * 501})

        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())

    def test_missing_reason_key_rejected_as_json_400(self):
        response = self.client.post(self.url, {})

        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())
