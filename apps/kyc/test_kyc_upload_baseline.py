"""Behavioral baseline for KYCUploadView.post (apps/kyc/views.py), originally
captured BEFORE migrating it to a Django Form (see the repo-wide validation
audit -- apps/kyc/KYCUploadView was identified as the next migration target),
now also serving as the regression suite verifying the migration to
apps/kyc/forms.py:KYCUploadForm preserved that behavior -- except for the one
explicitly approved kyc_consent fix (see below and KycConsentTests).

Everything here pins CURRENT behavior, warts included -- it does not assume
the Locker/Shipment pattern applies unchanged. Specific things verified by
reading the actual view before writing these (not assumed):

- doc_type and file presence are checked TOGETHER in one `if not doc_type or
  not file:` -- there is no separate "missing document_type" vs "missing
  document" message; both produce the same generic
  'Please select document type and file.' text.
- Check order is: (1) doc_type+file presence -> (2) kyc_consent presence ->
  (3) doc_type is a valid choice -> (4) validate_file_upload(file).
- Every error path does `return render(request, self.template_name)` --
  a 200 re-render of the upload template, NOT a redirect (unlike
  ApproveParcelView/RequestDiscardView, which redirect on error).
- Only ONE of the four error paths wraps a raised ValidationError and flashes
  str(e) -- the validate_file_upload failure (line ~54). The other three
  messages ('Please select document type and file.', 'Please consent...',
  'Invalid document type.') are plain hardcoded strings, never passed through
  ValidationError, so they do NOT get the "['...']" bracket-repr formatting
  Locker/Shipment's ValidationError-based messages have. This is verified
  directly from source, not assumed by analogy.
- The pre-migration view used `if not request.POST.get('kyc_consent'):`, a
  bare Python truthiness check on the raw string that let any non-empty
  string -- including 'off', 'false', '0' -- through exactly like 'on'.
  Only a missing key or empty string '' failed it. This was the same class
  of coercion risk already fixed for Shipment's signature_agree, and was
  explicitly approved to be fixed here too as part of this migration --
  KYCUploadForm.clean() now requires an exact match against the literal
  'on' the checkbox actually sends. See KycConsentTests below for the
  before/after split: test_consent_on_accepted and the blank/missing cases
  are unchanged; the 'off'/'false'/'0' cases now assert rejection instead
  of the old acceptance.
- `sanitize_filename(file.name)` is computed into `safe_filename` but that
  local variable is never passed to upload_kyc_document() or used anywhere
  else in the view -- its return value has no effect on any externally
  observable behavior today.

DeclarationService-style PDF/network calls aren't involved here, but the
real Supabase upload must still be mocked -- apps/kyc/views.py imports
`upload_kyc_document` directly into its own module namespace, so patching
`apps.kyc.views.upload_kyc_document` (same target the existing
KYCUploadConsentTests in apps/kyc/tests.py already use) is the correct and
consistent mock point.
"""
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import ConsentRecord, KYCDocument, Locker, User

MINIMAL_PDF = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF'


def make_pdf(name='doc.pdf', content=MINIMAL_PDF, content_type='application/pdf'):
    return SimpleUploadedFile(name, content, content_type=content_type)


class KYCUploadBaselineTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='kyc-baseline@example.com')
        Locker.objects.create(user=self.user)
        self.client.force_login(self.user)
        self.url = reverse('kyc:upload')

    def _post(self, **overrides):
        data = {'document_type': 'aadhaar', 'document': make_pdf(), 'kyc_consent': 'on'}
        data.update(overrides)
        return self.client.post(self.url, data)

    def _messages(self, response):
        return [str(m) for m in response.context['messages']]


# -- 1. Valid upload -------------------------------------------------------

class ValidUploadTests(KYCUploadBaselineTestCase):
    @patch('apps.kyc.views.upload_kyc_document', return_value='RB-1/aadhaar_x.pdf')
    def test_valid_upload_redirects_to_list(self, mock_upload):
        response = self._post()
        self.assertRedirects(response, reverse('kyc:list'))

    @patch('apps.kyc.views.upload_kyc_document', return_value='RB-1/aadhaar_x.pdf')
    def test_valid_upload_creates_pending_kyc_document(self, mock_upload):
        self._post(document_type='passport')

        doc = KYCDocument.objects.get(user=self.user)
        self.assertEqual(doc.document_type, 'passport')
        self.assertEqual(doc.status, 'pending')
        self.assertEqual(doc.document_url, 'RB-1/aadhaar_x.pdf')
        mock_upload.assert_called_once()

    @patch('apps.kyc.views.upload_kyc_document', return_value='RB-1/aadhaar_x.pdf')
    def test_valid_upload_creates_consent_record(self, mock_upload):
        self._post()

        record = ConsentRecord.objects.get(user=self.user, consent_type='kyc_upload')
        self.assertTrue(record.policy_version)
        self.assertIsNotNone(record.ip_address)

    @patch('apps.kyc.views.upload_kyc_document', return_value='RB-1/aadhaar_x.pdf')
    def test_valid_upload_logs_success_message(self, mock_upload):
        # Success redirects -- message is attached to the redirect target,
        # so follow=True is needed to read it from the final response.
        response = self.client.post(self.url, {
            'document_type': 'aadhaar', 'document': make_pdf(), 'kyc_consent': 'on',
        }, follow=True)
        self.assertIn('Document uploaded successfully! It will be reviewed shortly.', self._messages(response))


# -- 2. document_type -------------------------------------------------------

class DocumentTypeTests(KYCUploadBaselineTestCase):
    @patch('apps.kyc.views.upload_kyc_document', return_value='RB-1/x.pdf')
    def test_every_valid_document_type_choice_accepted(self, mock_upload):
        for code, _label in KYCDocument.DOCUMENT_TYPES:
            with self.subTest(document_type=code):
                KYCDocument.objects.filter(user=self.user).delete()
                response = self._post(document_type=code)
                self.assertRedirects(response, reverse('kyc:list'))
                self.assertEqual(KYCDocument.objects.get(user=self.user).document_type, code)

    def test_invalid_document_type_rejected_with_exact_plain_message(self):
        """'Invalid document type.' is a bare hardcoded string here -- NOT
        wrapped in ValidationError, so (unlike the file-validation error
        below) it is NOT bracket-formatted."""
        response = self._post(document_type='not_a_real_type')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Invalid document type.', self._messages(response))
        self.assertFalse(KYCDocument.objects.filter(user=self.user).exists())

    def test_blank_document_type_rejected_with_combined_presence_message(self):
        """An explicitly blank document_type ('') is falsy, so it's caught
        by the combined `if not doc_type or not file:` presence check --
        same generic message as a fully missing field, not 'Invalid document
        type.' (that check runs later and is never reached)."""
        response = self._post(document_type='')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Please select document type and file.', self._messages(response))
        self.assertFalse(KYCDocument.objects.filter(user=self.user).exists())

    def test_missing_document_type_key_rejected_with_combined_presence_message(self):
        data = {'document': make_pdf(), 'kyc_consent': 'on'}
        response = self.client.post(self.url, data)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Please select document type and file.', self._messages(response))
        self.assertFalse(KYCDocument.objects.filter(user=self.user).exists())


# -- 3. document upload ------------------------------------------------------

class DocumentFileTests(KYCUploadBaselineTestCase):
    @patch('apps.kyc.views.upload_kyc_document', return_value='RB-1/x.pdf')
    def test_valid_pdf_accepted(self, mock_upload):
        response = self._post(document=make_pdf())
        self.assertRedirects(response, reverse('kyc:list'))

    def test_disallowed_content_type_rejected(self):
        upload = make_pdf(name='doc.exe', content=b'MZfake', content_type='application/x-msdownload')
        response = self._post(document=upload)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(KYCDocument.objects.filter(user=self.user).exists())

    def test_extension_content_type_mismatch_rejected(self):
        upload = make_pdf(name='doc.txt', content=b'\x89PNG\r\n\x1a\nfakepng', content_type='image/png')
        response = self._post(document=upload)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(KYCDocument.objects.filter(user=self.user).exists())

    def test_magic_bytes_mismatch_rejected(self):
        upload = make_pdf(content=b'NOT_A_REAL_PDF_HEADER')
        response = self._post(document=upload)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(KYCDocument.objects.filter(user=self.user).exists())

    def test_oversized_file_rejected(self):
        from django.conf import settings
        max_size = getattr(settings, 'MAX_UPLOAD_SIZE', 5 * 1024 * 1024)
        oversized_content = b'%PDF-' + (b'A' * (max_size + 1))
        upload = make_pdf(content=oversized_content)
        response = self._post(document=upload)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(KYCDocument.objects.filter(user=self.user).exists())

    def test_file_rejection_message_is_bracket_formatted_validation_error(self):
        """The ONE error path in this view that wraps a real ValidationError:
        `except ValidationError as e: messages.error(request, str(e))`.
        Pinning the exact current bracket-repr format, matching the
        Locker/Shipment pattern for THIS specific path only."""
        upload = make_pdf(name='doc.exe', content=b'MZfake', content_type='application/x-msdownload')
        response = self._post(document=upload)

        messages = self._messages(response)
        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0].startswith('['))
        self.assertTrue(messages[0].endswith(']'))
        self.assertIn('Invalid file type', messages[0])

    def test_missing_document_key_rejected(self):
        """Missing document is currently REJECTED (combined presence check
        with document_type), not silently accepted."""
        data = {'document_type': 'aadhaar', 'kyc_consent': 'on'}
        response = self.client.post(self.url, data)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Please select document type and file.', self._messages(response))
        self.assertFalse(KYCDocument.objects.filter(user=self.user).exists())


# -- 4. kyc_consent -----------------------------------------------------------

class KycConsentTests(KYCUploadBaselineTestCase):
    def test_missing_consent_key_rejected(self):
        data = {'document_type': 'aadhaar', 'document': make_pdf()}
        response = self.client.post(self.url, data)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Please consent to processing this document to continue.', self._messages(response))
        self.assertFalse(KYCDocument.objects.filter(user=self.user).exists())

    def test_blank_consent_rejected(self):
        response = self._post(kyc_consent='')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Please consent to processing this document to continue.', self._messages(response))
        self.assertFalse(KYCDocument.objects.filter(user=self.user).exists())

    @patch('apps.kyc.views.upload_kyc_document', return_value='RB-1/x.pdf')
    def test_consent_on_accepted(self, mock_upload):
        response = self._post(kyc_consent='on')
        self.assertRedirects(response, reverse('kyc:list'))

    # -- Intentional behavior change (Forms migration, apps/kyc/forms.py
    # KYCUploadForm.clean()): the pre-migration view used a bare Python
    # truthiness check (`if not request.POST.get('kyc_consent'):`), which
    # let any non-empty string -- including 'off'/'false'/'0' -- through
    # exactly like 'on'. These three tests originally PINNED that risk
    # (test_consent_off_string_currently_accepted_not_rejected, etc.,
    # asserting acceptance) as a baseline before migration. The migration
    # was explicitly approved to fix this -- see KYCUploadForm's docstring
    # -- by requiring an exact match against 'on' (the literal value the
    # checkbox in templates/kyc/upload.html actually sends when checked).
    # These now assert the corrected behavior: rejection, same as blank
    # consent, matching the same fix already applied to Shipment's
    # signature_agree.

    def test_consent_off_string_now_rejected(self):
        response = self._post(kyc_consent='off')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Please consent to processing this document to continue.', self._messages(response))
        self.assertFalse(KYCDocument.objects.filter(user=self.user).exists())

    def test_consent_false_string_now_rejected(self):
        response = self._post(kyc_consent='false')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Please consent to processing this document to continue.', self._messages(response))
        self.assertFalse(KYCDocument.objects.filter(user=self.user).exists())

    def test_consent_zero_string_now_rejected(self):
        response = self._post(kyc_consent='0')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Please consent to processing this document to continue.', self._messages(response))
        self.assertFalse(KYCDocument.objects.filter(user=self.user).exists())

    def test_consent_other_truthy_values_rejected(self):
        for bad_value in ('true', '1', 'yes', 'On', 'ON', ' on', 'on '):
            with self.subTest(value=bad_value):
                KYCDocument.objects.filter(user=self.user).delete()
                response = self._post(kyc_consent=bad_value)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(KYCDocument.objects.filter(user=self.user).exists())


# -- 5. sanitize_filename() has no observable effect today -------------------

class SanitizeFilenameNoOpTests(KYCUploadBaselineTestCase):
    """Historical note: the view used to compute sanitize_filename(file.name)
    into an unused `safe_filename` local -- confirmed dead code (no side
    effects, result never referenced) and removed as a small cleanup. These
    tests originally pinned that the dead call had no observable effect;
    now that the call itself is gone, they continue to pin the same
    underlying fact directly: upload_kyc_document() is called with the
    original `file` object (locker_id/doc_type build the storage path, not
    the filename), so a dangerous/weird original filename must not change
    outcome or storage path versus a normal one."""

    @patch('apps.kyc.views.upload_kyc_document', return_value='RB-1/x.pdf')
    def test_path_traversal_filename_does_not_change_outcome(self, mock_upload):
        upload = make_pdf(name='../../etc/passwd.pdf')
        response = self._post(document=upload)

        self.assertRedirects(response, reverse('kyc:list'))
        mock_upload.assert_called_once()
        # The call succeeded identically to a normal filename -- the
        # dangerous name had no special effect (neither blocking nor
        # otherwise altering the upload).
        self.assertTrue(KYCDocument.objects.filter(user=self.user).exists())

    @patch('apps.kyc.views.upload_kyc_document', return_value='RB-1/x.pdf')
    def test_filename_with_special_characters_does_not_change_outcome(self, mock_upload):
        upload = make_pdf(name='my <script>doc.pdf')
        response = self._post(document=upload)

        self.assertRedirects(response, reverse('kyc:list'))
        self.assertTrue(KYCDocument.objects.filter(user=self.user).exists())


# -- 6. Error formatting summary (cross-check across all four error paths) --

class ErrorFormattingTests(KYCUploadBaselineTestCase):
    """Explicit cross-check: exactly one of the four error paths in this
    view is ValidationError-based (and thus bracket-formatted); the other
    three are plain strings. Verified directly from apps/kyc/views.py
    source, not assumed from the Locker/Shipment pattern."""

    def test_presence_message_not_bracket_formatted(self):
        data = {'document_type': ''}
        response = self.client.post(self.url, data)
        messages = self._messages(response)
        self.assertIn('Please select document type and file.', messages)
        self.assertFalse(messages[0].startswith('['))

    def test_consent_message_not_bracket_formatted(self):
        data = {'document_type': 'aadhaar', 'document': make_pdf()}
        response = self.client.post(self.url, data)
        messages = self._messages(response)
        self.assertIn('Please consent to processing this document to continue.', messages)
        self.assertFalse(messages[0].startswith('['))

    def test_invalid_choice_message_not_bracket_formatted(self):
        response = self._post(document_type='bogus')
        messages = self._messages(response)
        self.assertIn('Invalid document type.', messages)
        self.assertFalse(messages[0].startswith('['))

    def test_file_validation_message_is_bracket_formatted(self):
        upload = make_pdf(name='doc.exe', content=b'MZfake', content_type='application/x-msdownload')
        response = self._post(document=upload)
        messages = self._messages(response)
        self.assertTrue(messages[0].startswith('['))


# -- 7. Complements (not duplicates of) existing KYCUploadConsentTests -------
# apps/kyc/tests.py already covers: missing consent blocks upload (200,
# no doc created), and consent='on' succeeds + logs a ConsentRecord. This
# file's KycConsentTests above cover blank consent and the 'off'/'false'/'0'
# coercion-risk cases that the existing tests don't, without repeating the
# two cases tests.py already has.
