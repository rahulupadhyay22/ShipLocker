from django import forms
from django.core.exceptions import ValidationError

from indiabox.validators import validate_phone, validate_text_input
from .models import SavedAddress


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
