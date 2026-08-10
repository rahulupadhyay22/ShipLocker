from django import forms
from django.core.exceptions import ValidationError

from .models import PersonalShopRequest


class FormInputStylingMixin:
    """Applies the site's .form-input class to every widget except file/checkbox inputs.

    Also seeds `initial` for the declared (non-model) fields from the instance's
    type_details JSON when editing — ModelForm only auto-populates initial for real
    model fields, so without this every type_details-backed field (quantity, notes,
    item_description, ...) renders blank on the edit form and gets wiped on save.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            details = self.instance.type_details or {}
            for name in self.fields:
                if name in details and name not in self.initial:
                    self.initial[name] = details[name]
        for field in self.fields.values():
            if isinstance(field.widget, (forms.CheckboxInput, forms.FileInput, forms.ClearableFileInput)):
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

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.type_details = {
            'description': self.cleaned_data.get('description', ''),
        }
        if commit:
            instance.save()
        return instance


class BoutiquePurchaseForm(FormInputStylingMixin, forms.ModelForm):
    reference_image = forms.ImageField(required=False, label='Reference Image')
    item_description = forms.CharField(widget=forms.Textarea, label='Item Description')
    preferred_size = forms.CharField(required=False, max_length=50, label='Preferred Size')

    class Meta:
        model = PersonalShopRequest
        fields = ['boutique_name']
        labels = {'boutique_name': 'Boutique Name'}

    def clean_boutique_name(self):
        name = self.cleaned_data.get('boutique_name')
        if not name:
            raise ValidationError('Boutique name is required.')
        return name

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.type_details = {
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
    shop_phone = forms.CharField(max_length=20, label='Contact Number')
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
        description = self.cleaned_data.get('description')
        if not description:
            raise ValidationError('Description is required.')
        return description

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.type_details = {
            'description': self.cleaned_data.get('description', ''),
        }
        if commit:
            instance.save()
        return instance
