from django import forms
from django.core.exceptions import ValidationError

from indiabox.validators import validate_text_input, validate_phone
from .models import PersonalShopRequest


class FormInputStylingMixin:
    """Applies the site's .form-input class to every widget except file/checkbox inputs.

    Also seeds `initial` for the declared (non-model) fields from the instance's
    type_details JSON when editing — ModelForm only auto-populates initial for real
    model fields, so without this every type_details-backed field (quantity, notes,
    item_description, ...) renders blank on the edit form and gets wiped on save.
    """

    def __init__(self, *args, locker=None, **kwargs):
        self.locker = locker
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            details = self.instance.type_details or {}
            for name in self.fields:
                if name in details and name not in self.initial:
                    self.initial[name] = details[name]
        for field in self.fields.values():
            if isinstance(field.widget, (
                forms.CheckboxInput, forms.FileInput, forms.ClearableFileInput,
                forms.RadioSelect, forms.CheckboxSelectMultiple,
            )):
                continue
            existing = field.widget.attrs.get('class', '')
            field.widget.attrs['class'] = (existing + ' form-input').strip()


class ProductLinkForm(FormInputStylingMixin, forms.ModelForm):
    quantity = forms.IntegerField(required=False, min_value=1)
    size = forms.CharField(required=False, max_length=50, label='Size')
    colour = forms.CharField(required=False, max_length=50, label='Colour')
    notes = forms.CharField(required=False, widget=forms.Textarea, max_length=300, label='Additional Notes')

    class Meta:
        model = PersonalShopRequest
        fields = ['product_url']
        labels = {'product_url': 'Product URL'}

    def clean_product_url(self):
        url = self.cleaned_data.get('product_url')
        if not url:
            raise ValidationError('Product URL is required.')
        return url

    def clean_notes(self):
        notes = self.cleaned_data.get('notes', '')
        return validate_text_input(
            notes, field_name='Additional Notes', max_length=300, required=False,
        )

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.type_details = {
            'quantity': self.cleaned_data.get('quantity'),
            'size': self.cleaned_data.get('size', ''),
            'colour': self.cleaned_data.get('colour', ''),
            'notes': self.cleaned_data.get('notes', ''),
        }
        if commit:
            instance.save()
        return instance


class ImageSearchForm(FormInputStylingMixin, forms.ModelForm):
    reference_image = forms.ImageField(required=False, label='Reference Image')
    description = forms.CharField(required=False, widget=forms.Textarea, label='Description')

    class Meta:
        model = PersonalShopRequest
        fields = []

    def clean_description(self):
        description = self.cleaned_data.get('description', '')
        return validate_text_input(
            description, field_name='Description', max_length=500, required=False,
        )

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.type_details = {
            'description': self.cleaned_data.get('description', ''),
        }
        if commit:
            instance.save()
        return instance


class BoutiquePurchaseForm(FormInputStylingMixin, forms.ModelForm):
    SOURCE_CHOICES = [('new', 'New Boutique Purchase'), ('trunk', 'Select from My Trunk')]

    source_mode = forms.ChoiceField(
        choices=SOURCE_CHOICES, initial='new', required=False,
        widget=forms.RadioSelect, label='How would you like to proceed?',
    )
    source_parcel = forms.ModelChoiceField(
        queryset=None, required=False, label='Select a Parcel from Your Trunk',
    )
    reference_image = forms.ImageField(required=False, label='Reference Image')
    item_description = forms.CharField(required=False, widget=forms.Textarea, label='Item Description')
    preferred_size = forms.CharField(required=False, max_length=50, label='Preferred Size')

    class Meta:
        model = PersonalShopRequest
        fields = ['boutique_name']
        labels = {'boutique_name': 'Boutique Name'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['boutique_name'].required = False
        from apps.locker.models import Parcel
        base_qs = self.locker.parcels if self.locker else Parcel.objects.none()
        self.fields['source_parcel'].queryset = base_qs.exclude(status__in=['discarded', 'returned', 'shipped'])
        if self.instance.pk and self.instance.source_parcel_id:
            self.initial['source_mode'] = 'trunk'
            self.initial['source_parcel'] = self.instance.source_parcel_id

    def clean_item_description(self):
        # required=False here to preserve the conditional (not unconditional)
        # requirement enforced below in clean() -- this only adds the shared
        # length/dangerous-pattern checks on top, it must not itself reject
        # a blank value in 'trunk' mode.
        item_description = self.cleaned_data.get('item_description', '')
        return validate_text_input(
            item_description, field_name='Item Description', max_length=500, required=False,
        )

    def clean(self):
        cleaned = super().clean()
        cleaned['source_mode'] = cleaned.get('source_mode') or 'new'
        if cleaned['source_mode'] == 'trunk':
            if not cleaned.get('source_parcel'):
                self.add_error('source_parcel', 'Please select a parcel from your trunk.')
        else:
            if not cleaned.get('boutique_name'):
                self.add_error('boutique_name', 'Boutique name is required.')
            # 'item_description' not in self.errors: only genuinely blank/
            # missing values are "required" errors -- if clean_item_description
            # already raised (dangerous pattern, over-limit), it's not in
            # cleaned_data either, but reporting "required" on top of that
            # would be a redundant second error for one invalid input.
            if not cleaned.get('item_description') and 'item_description' not in self.errors:
                self.add_error('item_description', 'Item description is required.')
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        mode = self.cleaned_data.get('source_mode')
        parcel = self.cleaned_data.get('source_parcel')
        if mode == 'trunk':
            instance.source_parcel = parcel
            instance.boutique_name = ''
        else:
            instance.source_parcel = None
        instance.type_details = {
            'source_mode': mode,
            'item_description': self.cleaned_data.get('item_description', ''),
            'preferred_size': self.cleaned_data.get('preferred_size', ''),
        }
        if commit:
            instance.save()
        return instance


class LocalShopPurchaseForm(FormInputStylingMixin, forms.ModelForm):
    CITY_CHOICES = [('hyderabad', 'Hyderabad')]

    reference_image = forms.ImageField(required=False, label='Reference Image')
    city = forms.ChoiceField(
        choices=CITY_CHOICES, initial='hyderabad', label='City',
        widget=forms.Select(attrs={'class': 'ta-select-narrow'}),
    )
    shop_address = forms.CharField(widget=forms.Textarea, label='Shop Address / Landmark')
    maps_link = forms.URLField(required=False, label='Google Maps Link (Optional)')
    # required=False: the template label reads "(If known)"
    # (templates/personal_shop/request_form_local_shop.html) and the
    # sidebar tip for this request type says "Add a contact number so we
    # can confirm details" alongside "Mention the shop's open hours if
    # known" (SIDEBAR_TIPS['local_shop_purchase'] in views.py) -- both
    # phrase it as optional, not mandatory. The field previously had no
    # required=False, so Django's default required=True silently
    # contradicted its own label; this was a pre-existing authoring gap,
    # not an intentional requirement.
    shop_phone = forms.CharField(max_length=20, required=False, label='Contact Number')
    item_description = forms.CharField(widget=forms.Textarea, label='Item Description')

    class Meta:
        model = PersonalShopRequest
        fields = ['shop_name']
        labels = {'shop_name': 'Shop Name'}

    def clean_shop_name(self):
        name = self.cleaned_data.get('shop_name')
        if not name:
            raise ValidationError('Shop name is required.')
        return name

    def clean_shop_phone(self):
        # Optional field (see the field declaration's comment) -- only
        # validate format when a value was actually supplied, same
        # optional-phone pattern already used elsewhere (ProfileUpdateForm,
        # SavedAddressForm, ShipmentCreateForm.recipient_phone).
        phone = self.cleaned_data.get('shop_phone', '')
        if phone:
            validate_phone(phone)
        return phone

    def clean_shop_address(self):
        shop_address = self.cleaned_data.get('shop_address', '')
        return validate_text_input(
            shop_address, field_name='Shop Address', max_length=300, required=True,
        )

    def clean_item_description(self):
        item_description = self.cleaned_data.get('item_description', '')
        return validate_text_input(
            item_description, field_name='Item Description', max_length=500, required=True,
        )

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.type_details = {
            'city': self.cleaned_data.get('city', ''),
            'shop_address': self.cleaned_data.get('shop_address', ''),
            'maps_link': self.cleaned_data.get('maps_link', ''),
            'shop_phone': self.cleaned_data.get('shop_phone', ''),
            'item_description': self.cleaned_data.get('item_description', ''),
        }
        if commit:
            instance.save()
        return instance


class CustomRequestForm(FormInputStylingMixin, forms.ModelForm):
    description = forms.CharField(widget=forms.Textarea, max_length=500, label='Description')

    class Meta:
        model = PersonalShopRequest
        fields = []

    def clean_description(self):
        description = self.cleaned_data.get('description', '')
        return validate_text_input(
            description, field_name='Description', max_length=500, required=True,
        )

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.type_details = {
            'description': self.cleaned_data.get('description', ''),
        }
        if commit:
            instance.save()
        return instance
