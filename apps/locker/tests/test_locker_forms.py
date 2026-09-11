"""Unit tests for ParcelApprovalForm and ReasonForm (apps/locker/forms.py),
added as part of the manual-validation -> Django Forms migration for
ApproveParcelView, CreateReturnPaymentOrderView, and RequestDiscardView.

Pure form-validation logic -- no DB access, so these run as SimpleTestCase
and are unaffected by the Postgres test-database collation/teardown issues
seen elsewhere in this suite.
"""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from apps.locker.forms import ParcelApprovalForm, ReasonForm
from apps.locker.views import _first_form_error_message


class ParcelApprovalFormPresenceSentinelTests(SimpleTestCase):
    """A field key entirely absent from POST must leave the parcel field
    untouched (cleaned_data is None, the view's sentinel), distinct from
    the same field submitted as an empty string (validated and applied)."""

    def test_empty_post_all_fields_none(self):
        form = ParcelApprovalForm({})
        self.assertTrue(form.is_valid(), form.errors)
        for field in ('item_name', 'item_price', 'category', 'customs_description'):
            self.assertIsNone(form.cleaned_data[field])

    def test_present_blank_fields_are_empty_string_not_none(self):
        form = ParcelApprovalForm({'item_name': '', 'category': '', 'customs_description': ''})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['item_name'], '')
        self.assertEqual(form.cleaned_data['category'], '')
        self.assertEqual(form.cleaned_data['customs_description'], '')

    def test_missing_item_price_key_is_none(self):
        form = ParcelApprovalForm({'item_name': 'x'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['item_price'])

    def test_blank_item_price_is_none(self):
        """item_price uses a truthy check, not `is not None` -- '' behaves
        the same as a missing key (both -> None, skip validation)."""
        form = ParcelApprovalForm({'item_price': ''})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['item_price'])


class ParcelApprovalFormItemPriceTests(SimpleTestCase):
    def test_valid_price_accepted(self):
        form = ParcelApprovalForm({'item_price': '25.50'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(str(form.cleaned_data['item_price']), '25.50')

    def test_non_numeric_price_rejected(self):
        form = ParcelApprovalForm({'item_price': 'not-a-number'})
        self.assertFalse(form.is_valid())
        self.assertIn('item_price', form.errors)

    def test_negative_price_rejected(self):
        form = ParcelApprovalForm({'item_price': '-10.00'})
        self.assertFalse(form.is_valid())

    def test_nan_price_rejected(self):
        form = ParcelApprovalForm({'item_price': 'NaN'})
        self.assertFalse(form.is_valid())

    def test_too_many_decimal_places_rejected(self):
        form = ParcelApprovalForm({'item_price': '10.999'})
        self.assertFalse(form.is_valid())


class ParcelApprovalFormCategoryTests(SimpleTestCase):
    """Requirement: preserve the current category error text exactly:
    ['Invalid category.'] where the existing view behavior exposes it --
    the form-level message must be the plain 'Invalid category.' (matching
    error_messages['invalid_choice']); _first_form_error_message() is what
    wraps it into the exact legacy bracketed string the view flashes."""

    def test_invalid_category_rejected_with_plain_message(self):
        form = ParcelApprovalForm({'category': 'not_a_real_category'})
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors['category'], ['Invalid category.'])

    def test_invalid_category_view_message_is_bracketed_legacy_format(self):
        form = ParcelApprovalForm({'category': 'not_a_real_category'})
        form.is_valid()
        self.assertEqual(_first_form_error_message(form), "['Invalid category.']")

    def test_blank_category_accepted(self):
        form = ParcelApprovalForm({'category': ''})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['category'], '')

    def test_all_real_choices_accepted(self):
        from apps.locker.models import Parcel
        for code, _label in Parcel.CATEGORY_CHOICES:
            with self.subTest(category=code):
                form = ParcelApprovalForm({'category': code})
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data['category'], code)


class ParcelApprovalFormTextFieldsTests(SimpleTestCase):
    def test_item_name_script_tag_rejected(self):
        form = ParcelApprovalForm({'item_name': '<script>alert(1)</script>'})
        self.assertFalse(form.is_valid())
        self.assertIn('item_name', form.errors)

    def test_item_name_over_255_rejected(self):
        form = ParcelApprovalForm({'item_name': 'A' * 256})
        self.assertFalse(form.is_valid())

    def test_item_name_at_255_accepted(self):
        form = ParcelApprovalForm({'item_name': 'A' * 255})
        self.assertTrue(form.is_valid(), form.errors)

    def test_customs_description_iframe_rejected(self):
        form = ParcelApprovalForm({'customs_description': '<iframe src="evil.com"></iframe>'})
        self.assertFalse(form.is_valid())
        self.assertIn('customs_description', form.errors)

    def test_customs_description_over_2000_rejected(self):
        form = ParcelApprovalForm({'customs_description': 'A' * 2001})
        self.assertFalse(form.is_valid())

    def test_customs_description_at_2000_accepted(self):
        form = ParcelApprovalForm({'customs_description': 'A' * 2000})
        self.assertTrue(form.is_valid(), form.errors)


class ParcelApprovalFormInvoiceTests(SimpleTestCase):
    def test_valid_pdf_accepted(self):
        upload = SimpleUploadedFile('receipt.pdf', b'%PDF-1.4 fake content', content_type='application/pdf')
        form = ParcelApprovalForm({}, {'invoice': upload})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNotNone(form.cleaned_data['invoice'])

    def test_no_invoice_accepted(self):
        form = ParcelApprovalForm({}, {})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['invoice'])

    def test_disallowed_content_type_rejected(self):
        upload = SimpleUploadedFile('receipt.exe', b'MZfake', content_type='application/x-msdownload')
        form = ParcelApprovalForm({}, {'invoice': upload})
        self.assertFalse(form.is_valid())
        self.assertIn('invoice', form.errors)

    def test_magic_bytes_mismatch_rejected(self):
        upload = SimpleUploadedFile('receipt.pdf', b'NOT_A_REAL_PDF_HEADER', content_type='application/pdf')
        form = ParcelApprovalForm({}, {'invoice': upload})
        self.assertFalse(form.is_valid())
        self.assertIn('invoice', form.errors)


class ReasonFormTests(SimpleTestCase):
    """Covers both call sites: CreateReturnPaymentOrderView (required=True)
    and RequestDiscardView (required=False), same form class."""

    def test_required_true_blank_rejected(self):
        form = ReasonForm({'reason': ''}, required=True, field_name='Reason for return')
        self.assertFalse(form.is_valid())

    def test_required_true_missing_key_rejected(self):
        form = ReasonForm({}, required=True, field_name='Reason for return')
        self.assertFalse(form.is_valid())

    def test_required_true_valid_accepted(self):
        form = ReasonForm({'reason': 'Wrong size'}, required=True, field_name='Reason for return')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['reason'], 'Wrong size')

    def test_required_false_blank_accepted(self):
        form = ReasonForm({'reason': ''}, required=False, field_name='Reason for discard')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['reason'], '')

    def test_required_false_missing_key_accepted(self):
        form = ReasonForm({}, required=False, field_name='Reason for discard')
        self.assertTrue(form.is_valid(), form.errors)

    def test_dangerous_reason_rejected_regardless_of_required(self):
        form = ReasonForm({'reason': '<script>alert(1)</script>'}, required=False, field_name='Reason for discard')
        self.assertFalse(form.is_valid())

    def test_overlong_reason_rejected(self):
        form = ReasonForm({'reason': 'A' * 501}, required=True, field_name='Reason for return')
        self.assertFalse(form.is_valid())

    def test_field_name_appears_in_error_message(self):
        form = ReasonForm({'reason': ''}, required=True, field_name='Reason for return')
        form.is_valid()
        self.assertIn('Reason for return', form.errors['reason'][0])
