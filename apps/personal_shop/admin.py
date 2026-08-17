from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import admin
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils import timezone
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from apps.locker.models import Parcel, ParcelImage
from . import pricing
from .models import (
    PersonalShopRequest, PersonalShopImage, PersonalShopNote,
    PersonalShopQuotation, PersonalShopQuotationLineItem,
    QUOTATION_TYPE_ALLOWED_REQUEST_TYPES,
)

QUOTATION_TYPE_CHOICES_ORDER = ['purchase', 'research_fee', 'expense_advance']


def allowed_quotation_types_for(request_type):
    """Which quotation_type values are valid for a given request_type — same
    rule PersonalShopQuotation.clean()/save() enforce server-side, exposed
    here so the admin's Quotation type dropdown can filter itself to match
    instead of only rejecting an invalid pick after the fact."""
    return [
        qt for qt in QUOTATION_TYPE_CHOICES_ORDER
        if (allowed := QUOTATION_TYPE_ALLOWED_REQUEST_TYPES.get(qt)) is None or request_type in allowed
    ]

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
        'assigned_executive', 'refund_required', 'work_started_at', 'work_started_by', 'created_at',
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
        return super().get_queryset(request).select_related(
            'locker', 'locker__user', 'assigned_executive', 'parcel', 'active_quotation', 'work_started_by',
        )

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == 'request_type':
            formfield.help_text = (
                "Escalate image_search/cart_screenshot to custom_request when the search becomes "
                "extensive, or boutique_purchase to custom_request when it turns into cross-boutique "
                "sourcing, by changing this field directly — no separate escalation flow."
            )
        return formfield

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

    @admin.action(description='🔍 Mark as Searching / Work Started')
    def mark_searching(self, request, queryset):
        """One action covering two distinct events, merged for a shorter dropdown:
        - ordinary pre-quotation "we're looking into this" (any request type)
        - the PDF's work-started trigger for a paid research_fee/expense_advance
          quotation, which locks that fee as non-refundable (spec 10 §8/§10)

        Split per-row rather than treated identically, so a mixed selection
        doesn't regress an already-paid, work-eligible request's status back to
        'searching' — that request gets the work-started stamp instead, never both.
        A request that already has work_started_at set is excluded from *both*
        branches (nothing left to do, and it must not fall through to searching
        just because it no longer matches the "not yet started" filter).
        """
        advanced = queryset.filter(
            active_quotation__quotation_type__in=['research_fee', 'expense_advance'],
            active_quotation__status='approved',
        )
        advanced_ids = list(advanced.values_list('pk', flat=True))
        work_eligible = advanced.filter(work_started_at__isnull=True)
        work_eligible_ids = list(work_eligible.values_list('pk', flat=True))

        if work_eligible_ids:
            if request.user.has_perm('personal_shop.mark_work_started'):
                updated = work_eligible.update(work_started_at=timezone.now(), work_started_by=request.user)
                self.message_user(request, f"Marked {updated} request(s) as work started (fee now non-refundable).")
            else:
                self.message_user(
                    request,
                    f"Skipped {len(work_eligible_ids)} request(s) — marking work started requires the "
                    "personal_shop.mark_work_started permission.",
                    level='warning',
                )

        remaining = queryset.exclude(pk__in=advanced_ids)
        updated = remaining.update(status='searching', searching_started_at=timezone.now())
        if updated:
            self.message_user(request, f"Marked {updated} request(s) as searching.")

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


class PersonalShopQuotationAdminForm(forms.ModelForm):
    class Meta:
        model = PersonalShopQuotation
        fields = '__all__'

    def clean_quotation_type(self):
        value = self.cleaned_data['quotation_type']
        if self.instance.pk:
            current = PersonalShopQuotation.objects.get(pk=self.instance.pk)
            if current.status == 'approved' and current.quotation_type != value:
                raise forms.ValidationError(
                    "Cannot change the quotation type after it has been paid/approved."
                )
        return value


@admin.register(PersonalShopQuotation)
class PersonalShopQuotationAdmin(ModelAdmin):
    form = PersonalShopQuotationAdminForm
    list_display = ['request', 'quotation_type', 'status', 'total_amount', 'valid_until', 'created_at']
    list_filter = ['status', 'quotation_type']
    search_fields = ['request__display_id']
    autocomplete_fields = ['request']
    inlines = [PersonalShopQuotationLineItemInline]

    class Media:
        js = ('js/admin/personal_shop/admin_suggested_fee.js',)

    def get_urls(self):
        return [
            path(
                'suggested-fee/<uuid:request_id>/',
                self.admin_site.admin_view(self.suggested_fee_view),
                name='personal_shop_quotation_suggested_fee',
            ),
        ] + super().get_urls()

    def suggested_fee_view(self, request, request_id):
        # admin_view() (get_urls, above) only enforces is_active/is_staff, not
        # the model-level view permission ModelAdmin's own views check — add
        # it explicitly so a staff user scoped away from TrunkAssist can't
        # use this endpoint to read a request's type/allowed quotation types.
        if not self.has_view_permission(request):
            raise Http404
        shop_request = get_object_or_404(PersonalShopRequest, pk=request_id)
        product_value = None
        raw_value = request.GET.get('product_value')
        if raw_value:
            try:
                product_value = Decimal(raw_value)
                # Decimal('NaN')/Decimal('Infinity') parse without error but
                # raise InvalidOperation/Overflow later during the rate
                # comparison/multiplication in ServiceCharge.compute() —
                # reject them here instead of letting that surface as a 500.
                if not product_value.is_finite():
                    product_value = None
            except InvalidOperation:
                product_value = None
        fee = pricing.suggested_service_fee(shop_request.request_type, product_value)
        return JsonResponse({
            'fee': str(fee) if fee is not None else None,
            'request_type': shop_request.request_type,
            'allowed_quotation_types': allowed_quotation_types_for(shop_request.request_type),
        })

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if 'service_fee_amount' in form.base_fields:
            form.base_fields['service_fee_amount'].help_text = (
                "Auto-suggested when you pick a request above — you can always override. "
                f"Current rates: {self._current_rate_summary()}"
            )
        return form

    def _current_rate_summary(self):
        # Built live from ServiceCharge (apps/content) rather than hardcoded —
        # editing a rate on the Service Charges admin page is the whole point
        # of that model; a string literal here would immediately go stale the
        # first time an admin changes one.
        from apps.content.models import ServiceCharge

        parts = []
        for request_type, code in pricing.REQUEST_TYPE_TO_SERVICE_CHARGE_CODE.items():
            charge = ServiceCharge.objects.filter(code=code, is_active=True).first()
            if charge is None:
                continue
            if charge.charge_type == 'percentage':
                parts.append(f"{request_type} {charge.percentage_rate}% (min ₹{charge.amount})")
            else:
                parts.append(f"{request_type} flat ₹{charge.amount}")
        return ' · '.join(parts) if parts else "see the Service Charges admin page."

    def save_related(self, request, form, formsets, change):
        # Line items live in an inline formset — Django saves those only after
        # save_related is entered, so totals must be recomputed here, not in
        # save_model, or they'd be summed against the pre-edit line items.
        super().save_related(request, form, formsets, change)

        obj = form.instance
        subtotal = sum((item.line_total for item in obj.line_items.all()), start=0)
        obj.subtotal = subtotal
        # Total is computed per quotation_type, not a flat sum of every field —
        # research_fee/expense_advance are standalone upfront fees charged before
        # any shipping/purchase happens, so shipping/gateway/product costs don't
        # apply to them at all (not just "usually zero").
        if obj.quotation_type == 'research_fee':
            obj.total_amount = obj.research_fee_amount
        elif obj.quotation_type == 'expense_advance':
            obj.total_amount = obj.travel_expense_amount
        else:
            obj.total_amount = (
                subtotal + obj.domestic_shipping_amount + obj.service_fee_amount
                + obj.payment_gateway_charge
            )

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
                # Only stamp on first arrival — same "set once" convention the
                # model's own save() uses for every other status timestamp.
                # A second/third quotation (spec 10's research-fee-then-purchase
                # and expense-advance-then-settlement flows) re-enters
                # 'quotation_ready' after 'paid' already happened once; unconditionally
                # overwriting this would push the timeline's "Quotation Ready" step
                # later than its already-stamped "Paid" step, rendering out of order.
                if parent.quotation_ready_at is None:
                    parent.quotation_ready_at = timezone.now()
                parent.save()
