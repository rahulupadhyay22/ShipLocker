from django import forms

from indiabox.validators import validate_address, validate_phone, validate_email, validate_text_input
from .models import Shipment


class ShipmentCreateForm(forms.Form):
    """Address, contact, and customs e-signature fields for shipment
    creation. Not a ModelForm -- most of Shipment's fields (user,
    shipment_type, status, consolidation_fee*, declaration_signed_at/ip,
    declaration_version) are set server-side in the view, not submitted by
    the user, so a ModelForm would need almost everything excluded.

    Parcel selection and addon codes are deliberately NOT included here --
    they're an ownership/DB-state re-check under select_for_update() inside
    the view's transaction, not format validation (see CreateShipmentView.post).
    """

    recipient_name = forms.CharField(required=False)
    address_line1 = forms.CharField(required=False)
    address_line2 = forms.CharField(required=False)
    city = forms.CharField(required=False)
    state = forms.CharField(required=False)
    postal_code = forms.CharField(required=False)
    country = forms.CharField(required=False)
    recipient_phone = forms.CharField(required=False)
    recipient_email = forms.CharField(required=False)

    declaration_purpose = forms.ChoiceField(choices=Shipment.DECLARATION_PURPOSE_CHOICES)
    # CharField, not BooleanField: BooleanField.to_python() only treats
    # "false"/"0" (case-insensitive) as False and coerces every other
    # non-empty string -- including a literal "off" -- to True, so it
    # would silently accept a spoofed/malformed value here. strip=False so
    # clean_signature_agree below does the exact same raw-value comparison
    # as the pre-migration DeclarationService.validate_signature_fields
    # check (`post_data.get('signature_agree') != 'on'`) -- CharField's
    # default whitespace-stripping would otherwise let ' on'/'on ' through.
    # The template (see shipments/create.html) sets this hidden field to
    # the literal string 'on' via JS when the user signs.
    signature_agree = forms.CharField(required=False, strip=False)
    signature_name = forms.CharField(required=False)

    save_address = forms.BooleanField(required=False)
    address_label = forms.CharField(required=False)

    def clean_recipient_phone(self):
        phone = self.cleaned_data.get('recipient_phone', '')
        if phone:
            validate_phone(phone)
        return phone

    def clean_recipient_email(self):
        email = self.cleaned_data.get('recipient_email', '')
        if email:
            validate_email(email)
        return email

    def clean_signature_agree(self):
        if self.cleaned_data.get('signature_agree', '') != 'on':
            raise forms.ValidationError(
                'Please agree to the Customer Declaration & Authorization to continue.'
            )
        return True

    def clean_signature_name(self):
        # Rejects invalid/over-length input outright (dangerous-pattern
        # check + strict length cap) instead of the old silent
        # .strip()[:255] truncation -- an intentional behavior change,
        # see apps/shipments/tests/test_esign_declaration.py.
        return validate_text_input(
            self.cleaned_data.get('signature_name', ''),
            field_name='Signature name', min_length=1, max_length=255, required=True,
        )

    def clean(self):
        cleaned_data = super().clean()

        # validate_address() validates a whole dict at once (it can't
        # decompose into per-field clean_<x> methods) and returns the
        # validated/normalized dict back -- same call, same error messages
        # as the pre-migration view code. Left to propagate naturally;
        # Form.clean() -> Django attaches it as a non-field error.
        address_data = validate_address({
            'recipient_name': cleaned_data.get('recipient_name', ''),
            'address_line1': cleaned_data.get('address_line1', ''),
            'address_line2': cleaned_data.get('address_line2', ''),
            'city': cleaned_data.get('city', ''),
            'state': cleaned_data.get('state', ''),
            'postal_code': cleaned_data.get('postal_code', ''),
            'country': cleaned_data.get('country', ''),
        })
        cleaned_data.update(address_data)

        # address_label is submitted unconditionally by the template even
        # when "save this address" is unchecked, and is unused in that
        # case -- only validated when save_address is actually on, same
        # as the pre-migration behavior.
        if cleaned_data.get('save_address'):
            cleaned_data['address_label'] = validate_text_input(
                cleaned_data.get('address_label', ''),
                field_name='Address label', min_length=1, max_length=50, required=False,
            ).strip()
        else:
            cleaned_data['address_label'] = ''

        return cleaned_data
