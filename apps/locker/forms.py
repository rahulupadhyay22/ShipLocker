from django import forms

from indiabox.validators import (
    validate_text_input, validate_decimal_amount, validate_file_upload,
)
from .models import Parcel


class ParcelApprovalForm(forms.Form):
    """Fields ApproveParcelView.post updates on an existing, already-locked
    Parcel. Not a ModelForm: the view only patches 4 of Parcel's ~20 fields
    on an instance it already holds mid-transaction, and never calls
    form.save() -- the transaction/status-flip/invoice-upload sequencing
    stays entirely in the view, same as ShipmentCreateForm's rationale.

    Each of item_name/category/customs_description mirrors the pre-migration
    `if request.POST.get(field) is not None:` semantics: a field key that is
    entirely absent from POST must leave the parcel's existing value
    untouched, distinct from a field submitted as an empty string (which is
    validated and applied). clean_<field> below returns None for "not
    submitted" as a sentinel the view checks with `is not None` -- Django's
    own CharField.to_python() cannot distinguish these two cases, so this
    can't be expressed as a bare field default.
    """

    item_name = forms.CharField(required=False)
    item_price = forms.CharField(required=False)
    category = forms.ChoiceField(
        choices=Parcel.CATEGORY_CHOICES, required=False,
        error_messages={'invalid_choice': 'Invalid category.'},
    )
    customs_description = forms.CharField(required=False)
    invoice = forms.FileField(required=False)

    def clean_item_name(self):
        if 'item_name' not in self.data:
            return None
        return validate_text_input(
            self.data.get('item_name', ''), field_name='Item name',
            min_length=1, max_length=255, required=False,
        )

    def clean_item_price(self):
        raw = self.data.get('item_price')
        if not raw:
            return None
        return validate_decimal_amount(raw, field_name='Item price', max_digits=10, decimal_places=2)

    def clean_category(self):
        # ChoiceField's own Field.clean() already ran (and would have
        # raised, using error_messages['invalid_choice'] above, before this
        # method is even called) -- self.cleaned_data['category'] here is
        # already the validated choice, or '' if blank/not applicable.
        if 'category' not in self.data:
            return None
        return self.cleaned_data.get('category', '')

    def clean_customs_description(self):
        if 'customs_description' not in self.data:
            return None
        return validate_text_input(
            self.data.get('customs_description', ''), field_name='Customs description',
            min_length=1, max_length=2000, required=False,
        )

    def clean_invoice(self):
        invoice = self.cleaned_data.get('invoice')
        if invoice:
            validate_file_upload(invoice)
        return invoice


class ReasonForm(forms.Form):
    """Shared by CreateReturnPaymentOrderView (required=True, 'Reason for
    return') and RequestDiscardView (required=False, 'Reason for discard') --
    same single field, same validator, differing only in required-ness and
    field_name text, so one form covers both rather than two near-identical
    classes."""

    reason = forms.CharField(required=False)

    def __init__(self, *args, required=True, field_name='Reason', max_length=500, **kwargs):
        self._reason_required = required
        self._reason_field_name = field_name
        self._reason_max_length = max_length
        super().__init__(*args, **kwargs)

    def clean_reason(self):
        return validate_text_input(
            self.cleaned_data.get('reason', ''), field_name=self._reason_field_name,
            min_length=1, max_length=self._reason_max_length, required=self._reason_required,
        )
