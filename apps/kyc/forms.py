import logging

from django import forms
from django.core.exceptions import ValidationError

from indiabox.validators import validate_file_upload
from apps.accounts.models import KYCDocument

security_logger = logging.getLogger('security')


class KYCUploadForm(forms.Form):
    """document_type, document, and kyc_consent for KYCUploadView.post.

    All three fields are declared required=False -- the real validation
    (including a combined "both must be present" check spanning two
    fields, and a specific check ORDER: presence -> consent -> document_type
    choice -> file content) happens in clean(), mirroring the original
    view's sequential if/return chain field-for-field. Django's normal
    per-field required-handling can't express "either of these two missing
    produces one shared message" or enforce cross-field check order, so
    that sequencing is reproduced here deliberately rather than split
    across clean_<field> methods that Django would call in an order this
    view's baseline doesn't follow.

    Message text/format is preserved exactly for document_type/presence
    (plain strings, not ValidationError-wrapped -- confirmed from the
    pre-migration view source and pinned by the baseline tests) and for
    the file-validation failure (which -- and only which -- reproduces the
    "['...']" bracket-repr the original `messages.error(request, str(e))`
    produced, since only that one path was ever ValidationError-based).

    kyc_consent is the one deliberate, approved behavior CHANGE: the
    baseline pinned that the pre-migration view accepted any non-empty
    string ('off', 'false', '0', ...) as consent, via a bare Python
    truthiness check. That is fixed here -- only the literal 'on' the
    checkbox actually sends when checked (see templates/kyc/upload.html,
    a plain <input type="checkbox"> with no explicit value= attribute) is
    accepted; every other value, including 'off'/'false'/'0'/'true'/'1'/
    'yes', is rejected exactly like a missing/blank value.
    """

    document_type = forms.CharField(required=False)
    document = forms.FileField(required=False)
    kyc_consent = forms.CharField(required=False, strip=False)

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()

        doc_type = self.data.get('document_type')
        document = self.files.get('document')

        if not doc_type or not document:
            raise ValidationError('Please select document type and file.')

        # Deliberate fix: exact-match against the literal value a checked
        # checkbox actually sends, not a bare truthiness check -- see the
        # class docstring. 'off'/'false'/'0'/etc. are no longer accepted.
        if self.data.get('kyc_consent', '') != 'on':
            raise ValidationError('Please consent to processing this document to continue.')

        if doc_type not in dict(KYCDocument.DOCUMENT_TYPES):
            raise ValidationError('Invalid document type.')

        try:
            validate_file_upload(document)
        except ValidationError as e:
            # Same security-logging-on-rejection call the pre-migration
            # view made at this exact point (only for a file-validation
            # failure, not for the other three rejection reasons above).
            if self.user is not None:
                security_logger.warning(f"KYC upload rejected: {self.user.email} - {e}")
            # Reproduces the exact legacy `messages.error(request, str(e))`
            # bracket-repr text for this one path -- the only field whose
            # pre-migration error was ever ValidationError-based.
            raise ValidationError(str(e))

        cleaned_data['document_type'] = doc_type
        cleaned_data['document'] = document
        cleaned_data['kyc_consent'] = True
        return cleaned_data
