"""Behavioral baseline for ProfileView.post (apps/accounts/views.py),
originally captured BEFORE migrating it to a Django Form (repo-wide
validation audit -- ProfileView was identified as the next migration target
after Shipment/Locker/KYC), now also serving as the regression suite
verifying the migration to apps/accounts/forms.py:ProfileUpdateForm
preserved that behavior -- except for the one explicitly approved
phone/whatsapp_number "blank clears the field" fix (see
BlankValueClearsFieldTests below). Nothing here assumed the Locker/Shipment/
KYC pattern applies unchanged -- every claim below was verified directly
against apps/accounts/views.py, apps/accounts/models.py,
apps/accounts/forms.py, and templates/accounts/profile.html before writing
these tests.

Key things verified from source (not assumed):

- Both `full_name` AND `phone`/`whatsapp_number` are read as
  `request.POST.get(field, user.<field>)` -- i.e. a MISSING key falls back
  to the user's CURRENT value (which is then re-validated), not to an
  empty string. This differs from every prior migration (Shipment/Locker/
  KYC all treated a missing key as "leave untouched" without re-validating).
  templates/accounts/profile.html's <form> always submits all three fields
  pre-filled with the current value, so "missing key" is only reachable via
  a raw/non-browser POST, not the real UI -- still worth pinning.
- `full_name` uses `validate_text_input(..., min_length=2, max_length=255)`
  with NO `required=False` passed -- validate_text_input's own default is
  `required=True`, so a blank/whitespace-only full_name is rejected with
  "Full name is required."
- The pre-migration view gated `phone`/`whatsapp_number` behind a bare
  `if phone:` truthiness check BEFORE validate_phone was ever called, and
  the assignment (`user.phone = phone`) only happened inside that same
  truthy branch -- so a present-but-blank submission was silently skipped,
  and there was no way to CLEAR an existing value via this endpoint.
  This was explicitly approved to change as part of the migration:
  ProfileUpdateForm.clean() now always assigns (validating only when
  non-blank), so a present-but-blank value clears the field. A MISSING
  key still falls back to the current value exactly as before -- see
  MissingKeyPreservesCurrentValueTests vs BlankValueClearsFieldTests below
  for the exact split.
- Check order is sequential with an early return on the first failure:
  full_name -> phone -> whatsapp_number. `user.save()` is the very last
  line, called only if all three checks pass -- so even though
  `user.full_name = full_name` mutates the in-memory object before phone/
  whatsapp are checked, a later failure means .save() is never reached and
  nothing is persisted (naturally atomic without an explicit
  transaction.atomic(), since it's a single .save() call at the end).
- ALL THREE error paths here wrap a raised ValidationError and flash
  `str(e)` -- unlike KYC (where 3 of 4 paths were plain strings), every
  ProfileView error message DOES get the "['...']" bracket-repr format,
  matching validate_text_input/validate_phone's ValidationError.__str__().
- Both success AND error paths `return redirect('accounts:profile')` (302)
  -- unlike KYC (error=render, success=redirect). Messages must be read via
  follow=True.
- ProfileView takes no pk/lookup -- it operates solely on `request.user`,
  so ownership is inherent to the auth boundary (LoginRequiredMixin), not a
  separate ownership check to test the way Locker's parcel-pk views needed.
- GET renders `request.user.locker.*` context values, so a Locker must
  exist for the test user or a followed redirect back to the profile page
  (to read messages) will error -- every test here creates one.

Comparison with the existing apps/accounts/forms.py:SavedAddressForm
(documented in the final report, not re-derived per test) -- both reuse
`validate_phone`/`validate_text_input` from indiabox/validators.py, but
SavedAddressForm is a real ModelForm with `recipient_phone` REQUIRED
(SavedAddress.recipient_phone has no blank=True), while ProfileView's
phone/whatsapp_number are fully optional with the "silently skip if falsy"
gate above -- a materially different validation shape for the "same"
validator call.
"""
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Locker, User


def make_user_with_locker(email='profile-baseline@example.com', **extra):
    defaults = dict(email=email, full_name='Original Name')
    defaults.update(extra)
    user = User.objects.create(**defaults)
    Locker.objects.create(user=user)
    return user


class ProfileBaselineTestCase(TestCase):
    def setUp(self):
        self.user = make_user_with_locker()
        self.client.force_login(self.user)
        self.url = reverse('accounts:profile')

    def _post(self, **overrides):
        data = {'full_name': 'Updated Name', 'phone': '9876543210', 'whatsapp_number': '9876543211'}
        data.update(overrides)
        return self.client.post(self.url, data)

    def _messages(self, response):
        return [str(m) for m in response.context['messages']]


# -- 1. Valid profile update -------------------------------------------------

class ValidUpdateTests(ProfileBaselineTestCase):
    def test_valid_update_redirects_to_profile(self):
        response = self._post()
        self.assertRedirects(response, self.url)

    def test_valid_update_persists_all_three_fields(self):
        self._post(full_name='Jane Doe', phone='9111111111', whatsapp_number='9222222222')
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, 'Jane Doe')
        self.assertEqual(self.user.phone, '9111111111')
        self.assertEqual(self.user.whatsapp_number, '9222222222')

    def test_valid_update_shows_success_message(self):
        response = self.client.post(self.url, {
            'full_name': 'Jane Doe', 'phone': '9111111111', 'whatsapp_number': '9222222222',
        }, follow=True)
        self.assertIn('Profile updated successfully.', self._messages(response))


# -- 2. Missing/blank full_name ----------------------------------------------

class FullNameMissingBlankTests(ProfileBaselineTestCase):
    def test_blank_full_name_rejected(self):
        response = self._post(full_name='')
        self.assertRedirects(response, self.url)
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, 'Original Name')

    def test_blank_full_name_error_message(self):
        response = self.client.post(self.url, {
            'full_name': '', 'phone': '9876543210', 'whatsapp_number': '9876543211',
        }, follow=True)
        self.assertIn("['Full name is required.']", self._messages(response))

    def test_whitespace_only_full_name_rejected(self):
        response = self._post(full_name='   ')
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, 'Original Name')

    def test_missing_full_name_key_falls_back_to_current_value_and_succeeds(self):
        """Missing key -> request.POST.get('full_name', user.full_name)
        re-validates and re-assigns the EXISTING name -- a no-op that still
        succeeds, unlike a present-but-blank submission (rejected above)."""
        data = {'phone': '9876543210', 'whatsapp_number': '9876543211'}
        response = self.client.post(self.url, data)
        self.assertRedirects(response, self.url)
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, 'Original Name')

    def test_full_name_failure_prevents_save_of_other_valid_fields(self):
        """full_name is checked first; a failure returns before user.save()
        is ever reached, so a simultaneously-valid phone is NOT persisted --
        pinning the natural all-or-nothing behavior of the single .save()
        at the end of the view, with no explicit transaction.atomic()."""
        response = self._post(full_name='', phone='9111111111')
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '')


# -- 3. full_name length boundaries ------------------------------------------

class FullNameLengthTests(ProfileBaselineTestCase):
    def test_single_character_rejected(self):
        response = self._post(full_name='A')
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, 'Original Name')

    def test_two_characters_accepted(self):
        self._post(full_name='Al')
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, 'Al')

    def test_255_characters_accepted(self):
        name = 'A' * 255
        self._post(full_name=name)
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, name)

    def test_256_characters_rejected(self):
        response = self._post(full_name='A' * 256)
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, 'Original Name')

    def test_length_error_message_format(self):
        response = self.client.post(self.url, {
            'full_name': 'A', 'phone': '9876543210', 'whatsapp_number': '9876543211',
        }, follow=True)
        self.assertIn(
            "['Full name must be between 2 and 255 characters.']", self._messages(response)
        )


# -- 4. Dangerous text patterns in full_name ---------------------------------

class FullNameDangerousInputTests(ProfileBaselineTestCase):
    def test_script_tag_rejected(self):
        response = self._post(full_name='<script>alert(1)</script>')
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, 'Original Name')

    def test_javascript_url_rejected(self):
        response = self._post(full_name='javascript:alert(1)')
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, 'Original Name')

    def test_event_handler_rejected(self):
        response = self._post(full_name='<img onerror=alert(1)>')
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, 'Original Name')

    def test_dangerous_pattern_error_message(self):
        response = self.client.post(self.url, {
            'full_name': '<script>alert(1)</script>', 'phone': '9876543210', 'whatsapp_number': '9876543211',
        }, follow=True)
        self.assertIn("['Full name contains disallowed content.']", self._messages(response))


# -- 5/6/7. phone: invalid, valid, boundaries --------------------------------

class PhoneValidationTests(ProfileBaselineTestCase):
    def test_valid_phone_accepted(self):
        self._post(phone='9876543210')
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '9876543210')

    def test_valid_phone_with_country_code_accepted(self):
        self._post(phone='+919876543210')
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '+919876543210')

    def test_alphabetic_phone_rejected(self):
        response = self._post(phone='not-a-phone')
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '')

    def test_phone_error_message_format(self):
        response = self.client.post(self.url, {
            'full_name': 'Updated Name', 'phone': 'not-a-phone', 'whatsapp_number': '9876543211',
        }, follow=True)
        self.assertIn("['Invalid phone number format.']", self._messages(response))

    def test_six_digit_phone_rejected(self):
        """validate_phone requires 7-15 digits after stripping separators."""
        response = self._post(phone='123456')
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '')

    def test_seven_digit_phone_accepted(self):
        self._post(phone='1234567')
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '1234567')

    def test_fifteen_digit_phone_accepted(self):
        self._post(phone='123456789012345')
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '123456789012345')

    def test_sixteen_digit_phone_rejected(self):
        response = self._post(phone='1234567890123456')
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '')


# -- 8/9. whatsapp_number: invalid, valid ------------------------------------

class WhatsappValidationTests(ProfileBaselineTestCase):
    def test_valid_whatsapp_accepted(self):
        self._post(whatsapp_number='9876543211')
        self.user.refresh_from_db()
        self.assertEqual(self.user.whatsapp_number, '9876543211')

    def test_invalid_whatsapp_rejected(self):
        response = self._post(whatsapp_number='not-a-number')
        self.user.refresh_from_db()
        self.assertEqual(self.user.whatsapp_number, '')

    def test_whatsapp_error_message_format(self):
        response = self.client.post(self.url, {
            'full_name': 'Updated Name', 'phone': '9876543210', 'whatsapp_number': 'not-a-number',
        }, follow=True)
        self.assertIn("['Invalid phone number format.']", self._messages(response))


# -- 10. Blank phone/whatsapp_number behavior --------------------------------
#
# Intentional behavior change (Forms migration, apps/accounts/forms.py
# ProfileUpdateForm.clean()): the pre-migration view silently skipped a
# blank phone/whatsapp_number submission, leaving the prior value in the
# database untouched (assignment only happened inside `if phone:`). This
# was explicitly approved to change as part of this migration -- a
# present-but-blank value is now treated as "clear this field", matching
# how a normal profile edit is expected to behave. The two tests below
# originally pinned the OLD "leaves unchanged" behavior (asserting the
# prior value survived a blank submission); they now assert the corrected
# "clears the field" behavior instead. Missing-key semantics (falls back
# to the current value, unchanged from before) are covered separately in
# MissingKeyPreservesCurrentValueTests below.

class BlankValueClearsFieldTests(ProfileBaselineTestCase):
    def test_blank_phone_on_existing_value_clears_it(self):
        self.user.phone = '9000000000'
        self.user.save()

        response = self._post(phone='')
        self.assertRedirects(response, self.url)
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '')

    def test_blank_whatsapp_on_existing_value_clears_it(self):
        self.user.whatsapp_number = '9000000001'
        self.user.save()

        response = self._post(whatsapp_number='')
        self.assertRedirects(response, self.url)
        self.user.refresh_from_db()
        self.assertEqual(self.user.whatsapp_number, '')

    def test_blank_phone_clear_shows_success_message_not_a_validation_error(self):
        """Clearing is a valid, successful edit -- not a rejected value."""
        self.user.phone = '9000000000'
        self.user.save()

        response = self.client.post(self.url, {
            'full_name': 'Updated Name', 'phone': '', 'whatsapp_number': '9876543211',
        }, follow=True)
        self.assertIn('Profile updated successfully.', self._messages(response))
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '')


# -- 9. Missing POST fields preserve the existing stored value --------------

class MissingKeyPreservesCurrentValueTests(ProfileBaselineTestCase):
    """PRESERVED, not changed by the migration: a missing POST key still
    falls back to request.user's current value (re-validated), distinct
    from a present-but-blank value (which now clears, per above)."""

    def test_missing_phone_key_with_existing_value_reasserts_it_and_succeeds(self):
        self.user.phone = '9000000000'
        self.user.save()

        data = {'full_name': 'Updated Name', 'whatsapp_number': '9876543211'}
        response = self.client.post(self.url, data)
        self.assertRedirects(response, self.url)
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '9000000000')

    def test_missing_whatsapp_key_with_existing_value_reasserts_it_and_succeeds(self):
        self.user.whatsapp_number = '9000000001'
        self.user.save()

        data = {'full_name': 'Updated Name', 'phone': '9876543210'}
        response = self.client.post(self.url, data)
        self.assertRedirects(response, self.url)
        self.user.refresh_from_db()
        self.assertEqual(self.user.whatsapp_number, '9000000001')

    def test_missing_full_name_key_with_existing_value_reasserts_it(self):
        data = {'phone': '9876543210', 'whatsapp_number': '9876543211'}
        response = self.client.post(self.url, data)
        self.assertRedirects(response, self.url)
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, 'Original Name')

    def test_missing_key_vs_present_blank_now_diverge_for_a_nonblank_value(self):
        """The key behavioral split this migration had to get right:
        missing -> keeps '9000000000'; present-blank -> clears it. Same
        starting state, two different POSTs, two different outcomes."""
        self.user.phone = '9000000000'
        self.user.save()

        missing_response = self.client.post(self.url, {
            'full_name': 'Name A', 'whatsapp_number': '9876543211',
        })
        self.assertRedirects(missing_response, self.url)
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '9000000000')

        blank_response = self._post(full_name='Name B', phone='')
        self.assertRedirects(blank_response, self.url)
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '')

    def test_missing_and_blank_phone_still_equivalent_when_already_blank(self):
        """When the existing value is already blank, missing-key (falls
        back to '') and present-blank (explicit clear to '') necessarily
        produce the same observable outcome -- not because either one
        'skips', but because both paths independently land on ''."""
        response_missing = self.client.post(self.url, {
            'full_name': 'Name A', 'whatsapp_number': '9876543211',
        })
        self.assertRedirects(response_missing, self.url)
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '')

        response_blank = self._post(full_name='Name B', phone='')
        self.assertRedirects(response_blank, self.url)
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '')

        response_blank = self._post(full_name='Name B', phone='')
        self.assertRedirects(response_blank, self.url)
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '')


# -- 11. Differences between phone and whatsapp_number validation -----------

class PhoneVsWhatsappParityTests(ProfileBaselineTestCase):
    """Both fields call the exact same validate_phone() with no field-
    specific rules -- confirming there is no divergence to account for
    during migration."""

    def test_same_invalid_value_rejected_identically_for_both_fields(self):
        bad = 'abc'
        response_phone = self._post(phone=bad)
        self.user.refresh_from_db()
        phone_after = self.user.phone

        self.user.phone = ''
        self.user.whatsapp_number = ''
        self.user.save()

        response_whatsapp = self._post(whatsapp_number=bad)
        self.user.refresh_from_db()
        whatsapp_after = self.user.whatsapp_number

        self.assertEqual(phone_after, '')
        self.assertEqual(whatsapp_after, '')

    def test_same_valid_value_accepted_identically_for_both_fields(self):
        value = '9812345670'
        self._post(phone=value, whatsapp_number=value)
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, value)
        self.assertEqual(self.user.whatsapp_number, value)


# -- 13 (redirect/response already covered above) / 14. Authorization -------

class AuthorizationTests(TestCase):
    def test_anonymous_post_redirects_to_login(self):
        url = reverse('accounts:profile')
        response = self.client.post(url, {'full_name': 'Hacker'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_update_only_affects_the_authenticated_user(self):
        user_a = make_user_with_locker(email='user-a@example.com', full_name='User A')
        user_b = make_user_with_locker(email='user-b@example.com', full_name='User B')
        self.client.force_login(user_a)

        self.client.post(reverse('accounts:profile'), {
            'full_name': 'User A Updated', 'phone': '9876543210', 'whatsapp_number': '9876543211',
        })

        user_a.refresh_from_db()
        user_b.refresh_from_db()
        self.assertEqual(user_a.full_name, 'User A Updated')
        self.assertEqual(user_b.full_name, 'User B')
