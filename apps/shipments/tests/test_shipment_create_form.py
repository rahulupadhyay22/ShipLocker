"""
Unit tests for ShipmentCreateForm (apps/shipments/forms.py), added as part
of the manual-validation -> Django Forms migration for CreateShipmentView.

Pure form-validation logic -- no DB access, so these run as SimpleTestCase
and are unaffected by the local Postgres test-database collation blocker
that currently prevents `manage.py test` from creating a test DB.
"""
from django.test import SimpleTestCase

from apps.shipments.forms import ShipmentCreateForm


def valid_data(**overrides):
    data = {
        'recipient_name': 'Jane Doe',
        'address_line1': '123 Main St',
        'address_line2': '',
        'city': 'Mumbai',
        'state': 'Maharashtra',
        'postal_code': '400001',
        'country': 'India',
        'recipient_phone': '9876543210',
        'recipient_email': 'jane@example.com',
        'declaration_purpose': 'gift',
        'signature_agree': 'on',
        'signature_name': 'Jane Doe',
        'save_address': '',
        'address_label': '',
    }
    data.update(overrides)
    return data


class ShipmentCreateFormValidationTests(SimpleTestCase):
    """Requirement 12: 'which existing validators each field will use' --
    verifies each field is wired to the validator the audit specified."""

    def test_valid_submission_is_valid(self):
        form = ShipmentCreateForm(valid_data())
        self.assertTrue(form.is_valid(), form.errors)

    def test_missing_required_address_field_rejected(self):
        form = ShipmentCreateForm(valid_data(recipient_name=''))
        self.assertFalse(form.is_valid())

    def test_dangerous_markup_in_address_field_rejected(self):
        """validate_address()'s dangerous-pattern check, exercised via the form."""
        form = ShipmentCreateForm(valid_data(city='<script>alert(1)</script>'))
        self.assertFalse(form.is_valid())

    def test_invalid_postal_code_format_rejected(self):
        form = ShipmentCreateForm(valid_data(postal_code='!!!not-valid!!!'))
        self.assertFalse(form.is_valid())

    def test_invalid_phone_rejected(self):
        form = ShipmentCreateForm(valid_data(recipient_phone='abc'))
        self.assertFalse(form.is_valid())

    def test_blank_phone_allowed(self):
        """recipient_phone is optional -- validate_phone only runs when non-blank,
        matching the pre-migration `if recipient_phone:` guard."""
        form = ShipmentCreateForm(valid_data(recipient_phone=''))
        self.assertTrue(form.is_valid(), form.errors)

    def test_invalid_email_rejected(self):
        form = ShipmentCreateForm(valid_data(recipient_email='not-an-email'))
        self.assertFalse(form.is_valid())

    def test_blank_email_allowed(self):
        form = ShipmentCreateForm(valid_data(recipient_email=''))
        self.assertTrue(form.is_valid(), form.errors)

    def test_invalid_declaration_purpose_choice_rejected(self):
        form = ShipmentCreateForm(valid_data(declaration_purpose='not_a_real_choice'))
        self.assertFalse(form.is_valid())

    def test_missing_signature_agree_rejected(self):
        data = valid_data()
        del data['signature_agree']
        form = ShipmentCreateForm(data)
        self.assertFalse(form.is_valid())


class ShipmentCreateFormSignatureAgreeTests(SimpleTestCase):
    """Regression tests for the signature_agree coercion bug: a plain
    forms.BooleanField's to_python() treats any string other than
    "false"/"0" (case-insensitive) as truthy, so a literal "off" was being
    silently accepted as agreement. signature_agree is a CharField with a
    clean_signature_agree requiring the exact string 'on', matching the
    pre-migration DeclarationService.validate_signature_fields check
    (`post_data.get('signature_agree') != 'on'`)."""

    def test_on_accepted(self):
        form = ShipmentCreateForm(valid_data(signature_agree='on'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIs(form.cleaned_data['signature_agree'], True)

    def test_missing_rejected(self):
        data = valid_data()
        del data['signature_agree']
        form = ShipmentCreateForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn('signature_agree', form.errors)

    def test_off_rejected(self):
        """The exact regression: BooleanField coerced 'off' to True."""
        form = ShipmentCreateForm(valid_data(signature_agree='off'))
        self.assertFalse(form.is_valid())
        self.assertIn('signature_agree', form.errors)

    def test_false_string_rejected(self):
        form = ShipmentCreateForm(valid_data(signature_agree='false'))
        self.assertFalse(form.is_valid())
        self.assertIn('signature_agree', form.errors)

    def test_zero_string_rejected(self):
        form = ShipmentCreateForm(valid_data(signature_agree='0'))
        self.assertFalse(form.is_valid())
        self.assertIn('signature_agree', form.errors)

    def test_other_unexpected_values_rejected(self):
        for bad_value in ('true', 'On', 'ON', '1', 'yes', 'agreed', ' on', 'on ', ''):
            with self.subTest(value=bad_value):
                form = ShipmentCreateForm(valid_data(signature_agree=bad_value))
                self.assertFalse(form.is_valid())
                self.assertIn('signature_agree', form.errors)


class ShipmentCreateFormSignatureNameTests(SimpleTestCase):
    """Requirement 7: signature_name gap fix -- reject, don't truncate."""

    def test_blank_signature_name_rejected(self):
        form = ShipmentCreateForm(valid_data(signature_name=''))
        self.assertFalse(form.is_valid())

    def test_whitespace_only_signature_name_rejected(self):
        form = ShipmentCreateForm(valid_data(signature_name='   '))
        self.assertFalse(form.is_valid())

    def test_overlong_signature_name_rejected_not_truncated(self):
        long_name = 'A' * 300
        form = ShipmentCreateForm(valid_data(signature_name=long_name))
        self.assertFalse(form.is_valid())
        self.assertIn('signature_name', form.errors)

    def test_signature_name_with_script_tag_rejected(self):
        form = ShipmentCreateForm(valid_data(signature_name='<script>alert(1)</script>'))
        self.assertFalse(form.is_valid())
        self.assertIn('signature_name', form.errors)

    def test_signature_name_not_matching_any_user_field_is_accepted(self):
        """No identity/KYC check -- a nickname is fine as long as it passes
        the shared free-text validator, same spec requirement as before."""
        form = ShipmentCreateForm(valid_data(signature_name="Grandma's Helper"))
        self.assertTrue(form.is_valid(), form.errors)


class ShipmentCreateFormAddressLabelTests(SimpleTestCase):
    """address_label is submitted unconditionally by the template but only
    meaningful (and only validated) when save_address is checked."""

    def test_label_ignored_and_not_validated_when_save_address_off(self):
        form = ShipmentCreateForm(valid_data(save_address='', address_label='<script>x</script>'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['address_label'], '')

    def test_label_validated_when_save_address_on(self):
        form = ShipmentCreateForm(valid_data(save_address='on', address_label='<script>x</script>'))
        self.assertFalse(form.is_valid())

    def test_overlong_label_rejected_when_save_address_on(self):
        form = ShipmentCreateForm(valid_data(save_address='on', address_label='X' * 60))
        self.assertFalse(form.is_valid())

    def test_valid_label_accepted_when_save_address_on(self):
        form = ShipmentCreateForm(valid_data(save_address='on', address_label='Home'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['address_label'], 'Home')


class ShipmentCreateFormSingleSourceOfTruthTests(SimpleTestCase):
    """Requirement 5/6: cleaned_data must be the one place downstream code
    reads validated address/contact fields from -- these confirm the form
    actually returns the validated values (regression guard for the
    SavedAddress raw-POST-reuse bug fixed in CreateShipmentView.post)."""

    def test_cleaned_data_contains_validated_address_fields(self):
        form = ShipmentCreateForm(valid_data(state='Karnataka', address_line2='Flat 4B'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['state'], 'Karnataka')
        self.assertEqual(form.cleaned_data['address_line2'], 'Flat 4B')

    def test_cleaned_data_contains_validated_phone_and_email(self):
        form = ShipmentCreateForm(valid_data(recipient_phone='9876543210', recipient_email='a@b.com'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['recipient_phone'], '9876543210')
        self.assertEqual(form.cleaned_data['recipient_email'], 'a@b.com')
