from django import forms
from django.core.exceptions import ValidationError

from indiabox.validators import validate_phone, validate_text_input
from .models import SavedAddress


class ProfileUpdateForm(forms.Form):
    """full_name/phone/whatsapp_number for ProfileView.post. Not a
    ModelForm: a ModelForm bound to `instance=user` would apply Django's own
    field defaults/required semantics, which would erase the exact
    missing-vs-blank distinction this view depends on (see below) --
    forms.Form with the fallback handled explicitly in clean() is the only
    design that can express "missing key preserves the current value" and
    "present-but-blank clears it" as two different outcomes, since
    ModelForm's bound-instance initial data collapses that distinction
    before validation ever runs.

    Requires `user=` at construction (the current apps.accounts.models.User)
    -- both for the missing-key fallback and because phone/whatsapp_number
    are validated against indiabox.validators.validate_phone only when
    non-blank, exactly mirroring ProfileView's original manual `if phone:`
    gate.

    Field semantics (verified against the pre-migration view, pinned by
    apps/accounts/tests/test_profile_baseline.py):
    - full_name: missing POST key -> falls back to user.full_name (then
      re-validated, matching original behavior); present -- even blank --
      is used and validated as submitted. Blank/whitespace-only is REJECTED
      (validate_text_input's own required=True default) -- full_name has no
      "clear" semantics, unchanged from before.
    - phone / whatsapp_number: missing POST key -> falls back to the
      user's current value (re-validated if non-blank, exactly like
      full_name). Present-but-blank is an intentional, approved behavior
      CHANGE from the pre-migration view (which silently left the existing
      value untouched on a blank submission) -- a present, explicitly blank
      value now CLEARS the field, matching how a normal "delete my phone
      number" edit is expected to behave. Both fields remain fully
      optional; only a non-blank value is passed through validate_phone.

    Error text is deliberately re-wrapped as str(ValidationError(...)) at
    each check -- the exact "['...']" bracket-repr format the pre-migration
    view's `messages.error(request, str(e))` produced, matching the same
    deliberate-format-preservation approach used for Locker/KYC. Django's
    own form.errors would otherwise store the plain, unwrapped message.
    """

    full_name = forms.CharField(required=False)
    phone = forms.CharField(required=False, strip=False)
    whatsapp_number = forms.CharField(required=False, strip=False)

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()

        full_name = self.data.get('full_name', self.user.full_name)
        try:
            full_name = validate_text_input(
                full_name, field_name='Full name', min_length=2, max_length=255,
            )
        except ValidationError as e:
            raise ValidationError(str(e))
        cleaned_data['full_name'] = full_name

        phone = self.data.get('phone', self.user.phone)
        if phone:
            try:
                validate_phone(phone)
            except ValidationError as e:
                raise ValidationError(str(e))
        cleaned_data['phone'] = phone

        whatsapp_number = self.data.get('whatsapp_number', self.user.whatsapp_number)
        if whatsapp_number:
            try:
                validate_phone(whatsapp_number)
            except ValidationError as e:
                raise ValidationError(str(e))
        cleaned_data['whatsapp_number'] = whatsapp_number

        return cleaned_data


class SavedAddressForm(forms.ModelForm):
    class Meta:
        model = SavedAddress
        fields = [
            'label', 'is_default',
            'recipient_name', 'recipient_phone', 'recipient_email',
            'address_line1', 'address_line2', 'city', 'state', 'postal_code', 'country',
        ]
        widgets = {
            'label': forms.TextInput(attrs={'class': 'form-control', 'placeholder': "e.g. Home, Office"}),
            'recipient_name': forms.TextInput(attrs={'class': 'form-control'}),
            'recipient_phone': forms.TextInput(attrs={'class': 'form-control'}),
            'recipient_email': forms.EmailInput(attrs={'class': 'form-control'}),
            'address_line1': forms.TextInput(attrs={'class': 'form-control'}),
            'address_line2': forms.TextInput(attrs={'class': 'form-control'}),
            'city': forms.TextInput(attrs={'class': 'form-control'}),
            'state': forms.TextInput(attrs={'class': 'form-control'}),
            'postal_code': forms.TextInput(attrs={'class': 'form-control'}),
            'country': forms.TextInput(attrs={'class': 'form-control'}),
            'is_default': forms.CheckboxInput(),
        }

    def clean_recipient_phone(self):
        phone = self.cleaned_data['recipient_phone']
        validate_phone(phone)
        return phone

    def clean(self):
        cleaned_data = super().clean()
        optional_fields = {'label', 'address_line2', 'state'}
        for field in ['label', 'recipient_name', 'address_line1', 'address_line2', 'city', 'state', 'country']:
            if field not in cleaned_data:
                continue
            try:
                cleaned_data[field] = validate_text_input(
                    cleaned_data[field],
                    field_name=field.replace('_', ' ').title(),
                    min_length=1 if field in optional_fields else 2,
                    max_length=255,
                    required=field not in optional_fields,
                )
            except ValidationError as e:
                self.add_error(field, e)
        return cleaned_data
