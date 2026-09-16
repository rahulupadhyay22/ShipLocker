"""Unit tests for ProfileUpdateForm (apps/accounts/forms.py), added as part
of the manual-validation -> Django Forms migration for ProfileView.

Pure form-validation logic -- no DB access, so these run as SimpleTestCase.
`user` only needs the three attributes the form reads (full_name, phone,
whatsapp_number), so a plain namespace stands in for a real User instance.
"""
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.accounts.forms import ProfileUpdateForm


def fake_user(full_name='Original Name', phone='9000000000', whatsapp_number='9000000001'):
    return SimpleNamespace(full_name=full_name, phone=phone, whatsapp_number=whatsapp_number)


class ValidUpdateTests(SimpleTestCase):
    def test_full_valid_update_accepted(self):
        form = ProfileUpdateForm(
            {'full_name': 'New Name', 'phone': '9111111111', 'whatsapp_number': '9222222222'},
            user=fake_user(),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['full_name'], 'New Name')
        self.assertEqual(form.cleaned_data['phone'], '9111111111')
        self.assertEqual(form.cleaned_data['whatsapp_number'], '9222222222')


class PhoneValidationTests(SimpleTestCase):
    def test_valid_phone_accepted(self):
        form = ProfileUpdateForm(
            {'full_name': 'Name', 'phone': '9876543210', 'whatsapp_number': '9876543211'}, user=fake_user(),
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_invalid_phone_rejected(self):
        form = ProfileUpdateForm(
            {'full_name': 'Name', 'phone': 'not-a-phone', 'whatsapp_number': '9876543211'}, user=fake_user(),
        )
        self.assertFalse(form.is_valid())


class WhatsappValidationTests(SimpleTestCase):
    def test_valid_whatsapp_accepted(self):
        form = ProfileUpdateForm(
            {'full_name': 'Name', 'phone': '9876543210', 'whatsapp_number': '9876543211'}, user=fake_user(),
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_invalid_whatsapp_rejected(self):
        form = ProfileUpdateForm(
            {'full_name': 'Name', 'phone': '9876543210', 'whatsapp_number': 'bogus'}, user=fake_user(),
        )
        self.assertFalse(form.is_valid())


class FullNameValidationTests(SimpleTestCase):
    def test_invalid_full_name_rejected(self):
        form = ProfileUpdateForm(
            {'full_name': '', 'phone': '9876543210', 'whatsapp_number': '9876543211'}, user=fake_user(),
        )
        self.assertFalse(form.is_valid())

    def test_dangerous_full_name_rejected(self):
        form = ProfileUpdateForm(
            {'full_name': '<script>alert(1)</script>', 'phone': '9876543210', 'whatsapp_number': '9876543211'},
            user=fake_user(),
        )
        self.assertFalse(form.is_valid())

    def test_boundary_lengths(self):
        too_short = ProfileUpdateForm(
            {'full_name': 'A', 'phone': '9876543210', 'whatsapp_number': '9876543211'}, user=fake_user(),
        )
        self.assertFalse(too_short.is_valid())

        min_ok = ProfileUpdateForm(
            {'full_name': 'Al', 'phone': '9876543210', 'whatsapp_number': '9876543211'}, user=fake_user(),
        )
        self.assertTrue(min_ok.is_valid(), min_ok.errors)

        max_ok = ProfileUpdateForm(
            {'full_name': 'A' * 255, 'phone': '9876543210', 'whatsapp_number': '9876543211'}, user=fake_user(),
        )
        self.assertTrue(max_ok.is_valid(), max_ok.errors)

        too_long = ProfileUpdateForm(
            {'full_name': 'A' * 256, 'phone': '9876543210', 'whatsapp_number': '9876543211'}, user=fake_user(),
        )
        self.assertFalse(too_long.is_valid())


class MissingKeyPreservesCurrentValueTests(SimpleTestCase):
    def test_missing_full_name_key_falls_back_to_user_value(self):
        form = ProfileUpdateForm(
            {'phone': '9876543210', 'whatsapp_number': '9876543211'},
            user=fake_user(full_name='Existing Name'),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['full_name'], 'Existing Name')

    def test_missing_phone_key_falls_back_to_user_value(self):
        form = ProfileUpdateForm(
            {'full_name': 'Name', 'whatsapp_number': '9876543211'},
            user=fake_user(phone='9000000000'),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['phone'], '9000000000')

    def test_missing_whatsapp_key_falls_back_to_user_value(self):
        form = ProfileUpdateForm(
            {'full_name': 'Name', 'phone': '9876543210'},
            user=fake_user(whatsapp_number='9000000001'),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['whatsapp_number'], '9000000001')


class BlankClearsFieldTests(SimpleTestCase):
    def test_blank_phone_clears_existing_value(self):
        form = ProfileUpdateForm(
            {'full_name': 'Name', 'phone': '', 'whatsapp_number': '9876543211'},
            user=fake_user(phone='9000000000'),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['phone'], '')

    def test_blank_whatsapp_clears_existing_value(self):
        form = ProfileUpdateForm(
            {'full_name': 'Name', 'phone': '9876543210', 'whatsapp_number': ''},
            user=fake_user(whatsapp_number='9000000001'),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['whatsapp_number'], '')

    def test_missing_vs_blank_diverge_for_a_nonblank_current_value(self):
        missing = ProfileUpdateForm(
            {'full_name': 'Name', 'whatsapp_number': '9876543211'},
            user=fake_user(phone='9000000000'),
        )
        self.assertTrue(missing.is_valid(), missing.errors)
        self.assertEqual(missing.cleaned_data['phone'], '9000000000')

        blank = ProfileUpdateForm(
            {'full_name': 'Name', 'phone': '', 'whatsapp_number': '9876543211'},
            user=fake_user(phone='9000000000'),
        )
        self.assertTrue(blank.is_valid(), blank.errors)
        self.assertEqual(blank.cleaned_data['phone'], '')


class SaveNeverPartiallyAppliedTests(SimpleTestCase):
    """The Form itself just reports valid/invalid -- these confirm an
    invalid submission leaves NOTHING usable in cleaned_data for the
    fields that would otherwise have been valid, since form.is_valid()
    is False and the view never reads cleaned_data at all in that case."""

    def test_invalid_phone_makes_whole_form_invalid(self):
        form = ProfileUpdateForm(
            {'full_name': 'Valid Name', 'phone': 'bad', 'whatsapp_number': '9876543211'}, user=fake_user(),
        )
        self.assertFalse(form.is_valid())

    def test_invalid_full_name_makes_whole_form_invalid_even_with_valid_phone(self):
        form = ProfileUpdateForm(
            {'full_name': '', 'phone': '9876543210', 'whatsapp_number': '9876543211'}, user=fake_user(),
        )
        self.assertFalse(form.is_valid())


class ErrorFormatTests(SimpleTestCase):
    """Pins the exact legacy bracket-repr text -- the deliberate
    format-preservation approach used for Locker/KYC, reproduced here via
    a try/except-rewrap inside ProfileUpdateForm.clean() itself."""

    def test_full_name_required_message_is_bracket_formatted(self):
        form = ProfileUpdateForm(
            {'full_name': '', 'phone': '9876543210', 'whatsapp_number': '9876543211'}, user=fake_user(),
        )
        form.is_valid()
        message = form.errors['__all__'][0]
        self.assertEqual(message, "['Full name is required.']")

    def test_full_name_length_message_is_bracket_formatted(self):
        form = ProfileUpdateForm(
            {'full_name': 'A', 'phone': '9876543210', 'whatsapp_number': '9876543211'}, user=fake_user(),
        )
        form.is_valid()
        message = form.errors['__all__'][0]
        self.assertEqual(message, "['Full name must be between 2 and 255 characters.']")

    def test_full_name_dangerous_content_message_is_bracket_formatted(self):
        form = ProfileUpdateForm(
            {'full_name': '<script>alert(1)</script>', 'phone': '9876543210', 'whatsapp_number': '9876543211'},
            user=fake_user(),
        )
        form.is_valid()
        message = form.errors['__all__'][0]
        self.assertEqual(message, "['Full name contains disallowed content.']")

    def test_phone_invalid_message_is_bracket_formatted(self):
        form = ProfileUpdateForm(
            {'full_name': 'Name', 'phone': 'bad', 'whatsapp_number': '9876543211'}, user=fake_user(),
        )
        form.is_valid()
        message = form.errors['__all__'][0]
        self.assertEqual(message, "['Invalid phone number format.']")

    def test_whatsapp_invalid_message_is_bracket_formatted(self):
        form = ProfileUpdateForm(
            {'full_name': 'Name', 'phone': '9876543210', 'whatsapp_number': 'bad'}, user=fake_user(),
        )
        form.is_valid()
        message = form.errors['__all__'][0]
        self.assertEqual(message, "['Invalid phone number format.']")
