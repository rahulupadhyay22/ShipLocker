from django import forms
from django.contrib import admin
from django.db import transaction
from django.utils import timezone
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from apps.locker.models import Parcel, ParcelImage
from .models import (
    PersonalShopRequest, PersonalShopImage, PersonalShopNote,
    PersonalShopQuotation, PersonalShopQuotationLineItem,
)

STATUS_COLORS = {
    'submitted': 'warning',
    'reviewing': 'warning',
    'executive_assigned': 'info',
    'searching': 'info',
    'quotation_ready': 'warning',
    'quotation_declined': 'danger',
    'quotation_expired': 'danger',
    'payment_pending': 'warning',
    'paid': 'success',
    'purchased': 'success',
    'delivered_to_warehouse': 'success',
    'added_to_trunk': 'success',
    'cancelled': 'danger',
    'needs_info': 'danger',
}


class PersonalShopImageForm(forms.ModelForm):
    image_file = forms.ImageField(required=False, label='Upload Image')

    class Meta:
        model = PersonalShopImage
        fields = ['request', 'caption']

    def save(self, commit=True):
        instance = super().save(commit=False)
        image_file = self.cleaned_data.get('image_file')
        if image_file:
            from .utils import upload_personal_shop_image
            instance.image_path = upload_personal_shop_image(
                file=image_file,
                locker_id=instance.request.locker.locker_id,
                request_display_id=instance.request.display_id,
            )
        if commit:
            instance.save()
        return instance


class PersonalShopImageInline(TabularInline):
    model = PersonalShopImage
    form = PersonalShopImageForm
    extra = 1
    readonly_fields = ['image_path', 'uploaded_at', 'image_preview']
    fields = ['image_file', 'image_path', 'caption', 'image_preview', 'uploaded_at']

    def image_preview(self, obj):
        if obj.image_path:
            url = obj.image_url
            if url:
                return format_html(
                    '<a href="{}" target="_blank"><img src="{}" style="max-height: 80px; border-radius: 4px;"/></a>',
                    url, url,
                )
        return '-'
    image_preview.short_description = 'Preview'


class PersonalShopNoteInline(TabularInline):
    model = PersonalShopNote
    fields = ['author', 'message', 'created_at']
    readonly_fields = ['created_at']
    extra = 1


class PersonalShopQuotationLineItemInline(TabularInline):
    model = PersonalShopQuotationLineItem
    fields = ['name', 'thumbnail_url', 'variant_details', 'qty', 'unit_amount']
    extra = 1


@admin.register(PersonalShopRequest)
class PersonalShopRequestAdmin(ModelAdmin):
    list_display = [
        'display_id', 'trunk_id', 'customer_name', 'request_type', 'status_badge',
        'assigned_executive', 'refund_required', 'created_at',
    ]
    list_filter = ['status', 'request_type', 'refund_required', 'created_at']
    search_fields = ['display_id', 'locker__locker_id', 'locker__user__email', 'shop_name', 'boutique_name', 'product_url']
    autocomplete_fields = ['locker', 'assigned_executive', 'parcel']
    readonly_fields = ['display_id', 'trunk_id', 'customer_name', 'created_at', 'updated_at']
    inlines = [PersonalShopImageInline, PersonalShopNoteInline]
    date_hierarchy = 'created_at'
    list_per_page = 25

    actions = ['assign_to_me', 'mark_searching', 'mark_needs_info', 'mark_delivered_to_warehouse']

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('locker', 'locker__user', 'assigned_executive', 'parcel')

    @display(description='Status', ordering='status', label=STATUS_COLORS)
    def status_badge(self, obj):
        return obj.status

    def trunk_id(self, obj):
        if obj is None:
            return '-'
        return obj.locker.locker_id
    trunk_id.short_description = 'Trunk ID'
    trunk_id.admin_order_field = 'locker__locker_id'

    def customer_name(self, obj):
        if obj is None:
            return '-'
        return obj.locker.user.get_full_name()
    customer_name.short_description = 'Customer'
    customer_name.admin_order_field = 'locker__user__full_name'

    @admin.action(description='🙋 Assign to me')
    def assign_to_me(self, request, queryset):
        queryset.filter(status__in=['submitted', 'reviewing']).update(
            assigned_executive=request.user, status='executive_assigned',
            executive_assigned_at=timezone.now(),
        )

    @admin.action(description='🔍 Mark as Searching')
    def mark_searching(self, request, queryset):
        queryset.update(status='searching', searching_started_at=timezone.now())

    @admin.action(description='⚠️ Mark as Needs Info')
    def mark_needs_info(self, request, queryset):
        queryset.update(status='needs_info')

    @admin.action(description='📦 Mark Delivered to Warehouse')
    def mark_delivered_to_warehouse(self, request, queryset):
        for obj in queryset.filter(parcel__isnull=True, status='purchased'):
            parcel = Parcel.objects.create(
                locker=obj.locker,
                status='approved',
                item_name=obj.shop_name or obj.boutique_name or obj.display_id,
                approved_at=timezone.now(),
            )
            ParcelImage.objects.bulk_create([
                ParcelImage(
                    parcel=parcel,
                    image_path=image.image_path,
                    caption=image.caption,
                    is_primary=(index == 0),
                )
                for index, image in enumerate(obj.images.order_by('uploaded_at'))
            ])
            now = timezone.now()
            obj.parcel = parcel
            obj.status = 'added_to_trunk'
            obj.delivered_at = now
            obj.added_to_trunk_at = now
            obj.save()


@admin.register(PersonalShopQuotation)
class PersonalShopQuotationAdmin(ModelAdmin):
    list_display = ['request', 'status', 'total_amount', 'valid_until', 'created_at']
    list_filter = ['status']
    search_fields = ['request__display_id']
    autocomplete_fields = ['request']
    inlines = [PersonalShopQuotationLineItemInline]

    def save_related(self, request, form, formsets, change):
        # Line items live in an inline formset — Django saves those only after
        # save_related is entered, so totals must be recomputed here, not in
        # save_model, or they'd be summed against the pre-edit line items.
        super().save_related(request, form, formsets, change)

        obj = form.instance
        subtotal = sum((item.line_total for item in obj.line_items.all()), start=0)
        obj.subtotal = subtotal
        obj.total_amount = subtotal + obj.domestic_shipping_amount + obj.service_fee_amount + obj.payment_gateway_charge

        # Extending valid_until on an already-expired quotation should bring it back
        # to life — otherwise it stays 'expired' forever since the auto-expire check
        # in PersonalShopQuotationView only runs while status is still 'pending'.
        if obj.status == 'expired' and obj.valid_until > timezone.now():
            obj.status = 'pending'

        obj.save(update_fields=['subtotal', 'total_amount', 'status'])

        if obj.status == 'pending':
            with transaction.atomic():
                parent = type(obj.request).objects.select_for_update().get(pk=obj.request.pk)
                parent.quotations.filter(status='pending').exclude(pk=obj.pk).update(status='declined')
                parent.active_quotation = obj
                parent.status = 'quotation_ready'
                parent.quotation_ready_at = timezone.now()
                parent.save()
