from django.contrib import admin
from django.utils.html import format_html, mark_safe
from django import forms
from decimal import Decimal
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display
from .models import Shipment, ShipmentItem, ShipmentDocument, TrackingEvent
from apps.payments.models import StorageFee
from apps.payments.services import _get_daily_storage_fee_amount


# ---- Shipment status color map ----
SHIPMENT_STATUS_COLORS = {
    'draft':              ('badge-draft',       '📝'),
    'declaration_pending': ('badge-declaration', '📄'),
    'pending_payment':    ('badge-pending-pay',  '💳'),
    'packing':            ('badge-packing',      '📦'),
    'dispatched':         ('badge-dispatched',   '🚀'),
    'in_transit':         ('badge-in-transit',   '✈️'),
    'customs':            ('badge-customs',      '🛃'),
    'out_for_delivery':   ('badge-out-delivery', '🚚'),
    'delivered':          ('badge-delivered',     '✅'),
    'returned':           ('badge-returned',     '↩️'),
    'cancelled':          ('badge-cancelled',    '❌'),
}


class ShipmentItemInline(TabularInline):
    model = ShipmentItem
    extra = 1
    raw_id_fields = ['parcel']
    readonly_fields = ['added_at']


class ShipmentDocumentForm(forms.ModelForm):
    """Custom form for ShipmentDocument with file upload."""
    document_file = forms.FileField(required=False, label='Upload Document')
    
    class Meta:
        model = ShipmentDocument
        fields = ['document_type', 'document_url']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make document_url optional — it can be auto-filled from file upload
        self.fields['document_url'].required = False
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        
        # Handle file upload
        document_file = self.cleaned_data.get('document_file')
        if document_file:
            from apps.locker.utils import upload_shipment_document
            
            # Get locker_id from shipment's user
            locker_id = instance.shipment.user.locker.locker_id if hasattr(instance.shipment.user, 'locker') else str(instance.shipment.user.id)[:8]
            shipment_id = instance.shipment.display_id
            doc_type = instance.document_type
            
            # Upload to Supabase
            file_path = upload_shipment_document(
                file=document_file,
                locker_id=locker_id,
                shipment_display_id=shipment_id,
                doc_type=doc_type
            )
            instance.document_url = file_path
        
        if commit:
            instance.save()
        return instance


class ShipmentDocumentInline(TabularInline):
    model = ShipmentDocument
    form = ShipmentDocumentForm
    extra = 1
    readonly_fields = ['uploaded_at', 'document_link']
    fields = ['document_file', 'document_type', 'document_url', 'document_link', 'uploaded_at']
    
    def document_link(self, obj):
        if obj.document_url:
            from apps.locker.utils import get_signed_shipment_doc_url
            try:
                signed_url = get_signed_shipment_doc_url(obj.document_url)
                return format_html('<a href="{}" target="_blank">📄 View Document</a>', signed_url)
            except:
                return "-"
        return "-"
    document_link.short_description = "View"


class TrackingEventInline(TabularInline):
    """Inline for viewing tracking events in Shipment admin."""
    model = TrackingEvent
    extra = 0
    readonly_fields = ['status', 'description', 'location', 'event_timestamp', 'created_at']
    ordering = ['-event_timestamp']
    
    def has_add_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False


class ShipmentAdminForm(forms.ModelForm):
    apply_storage_fee = forms.BooleanField(
        required=False,
        label='Apply storage fee on save',
        help_text='Enable this and click Save to create or update a pending storage fee from this shipment page.'
    )
    manual_storage_fee_amount = forms.DecimalField(
        required=False,
        min_value=Decimal('0.01'),
        decimal_places=2,
        max_digits=10,
        label='Storage fee amount override (optional)',
        help_text='Leave blank to auto-calculate from Service Charges (daily storage rate × overdue days).'
    )
    manual_storage_fee_days = forms.IntegerField(
        required=False,
        min_value=1,
        label='Overdue days',
        help_text='Optional override. If blank, overdue days are auto-calculated per parcel (storage days - 30).'
    )
    apply_storage_fee_to_all_parcels = forms.BooleanField(
        required=False,
        label='Apply fee to all parcels (otherwise first parcel only)',
        help_text='Checked: apply to all overdue parcels in this shipment. Unchecked: apply only to the first overdue parcel.'
    )

    class Meta:
        model = Shipment
        fields = '__all__'


@admin.register(Shipment)
class ShipmentAdmin(ModelAdmin):
    form = ShipmentAdminForm
    list_display = ['display_id', 'user', 'shipment_type', 'status_badge', 'payment_badge', 'carrier', 'tracking_number', 'item_count_display', 'created_at']
    list_filter = ['status', 'shipment_type', 'carrier', 'created_at']
    search_fields = ['display_id', 'user__email', 'tracking_number', 'recipient_name']
    readonly_fields = ['display_id', 'created_at', 'updated_at', 'paid_at', 'cancelled_at']
    raw_id_fields = ['user']
    inlines = [ShipmentItemInline, ShipmentDocumentInline, TrackingEventInline]
    date_hierarchy = 'created_at'
    list_per_page = 25
    
    fieldsets = (
        ('Shipment Info', {
            'fields': ('user', 'shipment_type', 'status', 'payment_status')
        }),
        ('Carrier & Tracking', {
            'fields': ('carrier', 'tracking_number', 'tracking_url')
        }),
        ('Recipient', {
            'fields': ('recipient_name', 'recipient_phone', 'recipient_email',
                      'address_line1', 'address_line2', 'city', 'state', 'postal_code', 'country')
        }),
        ('Weight & Cost', {
            'fields': (
                'total_weight_kg', 'shipping_cost', 'currency', 'estimated_delivery_date',
                'apply_storage_fee', 'manual_storage_fee_amount', 'manual_storage_fee_days',
                'apply_storage_fee_to_all_parcels'
            )
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'dispatched_at', 'delivered_at', 'paid_at'),
            'classes': ('collapse',)
        }),
        ('Cancellation', {
            'fields': ('cancelled_at', 'cancelled_reason'),
            'classes': ('collapse',)
        }),
        ('Notes', {
            'fields': ('admin_notes',),
            'classes': ('collapse',)
        }),
    )
    
    actions = [
        'approve_declaration', 'mark_packing', 'mark_dispatched', 'mark_delivered',
        'add_storage_fees'
    ]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

        apply_storage_fee = form.cleaned_data.get('apply_storage_fee')
        manual_fee_amount = form.cleaned_data.get('manual_storage_fee_amount')
        manual_overdue_days = form.cleaned_data.get('manual_storage_fee_days')
        apply_all = form.cleaned_data.get('apply_storage_fee_to_all_parcels')

        if not apply_storage_fee:
            return

        shipment_items = list(obj.items.select_related('parcel').all())
        if not shipment_items:
            self.message_user(request, 'Storage fee not applied: no parcels in this shipment.', level='warning')
            return

        overdue_items = [
            item for item in shipment_items
            if item.parcel and item.parcel.storage_days > 30
        ]
        if not overdue_items:
            self.message_user(
                request,
                'Storage fee not applied: no parcel has crossed 30 days free storage.',
                level='warning'
            )
            return

        target_items = overdue_items if apply_all else overdue_items[:1]
        created_count = 0
        updated_count = 0
        currency = obj.currency or 'INR'
        daily_fee = _get_daily_storage_fee_amount()

        for item in target_items:
            parcel = item.parcel
            if not parcel:
                continue

            overdue_days = manual_overdue_days or max(parcel.storage_days - 30, 1)
            fee_amount = manual_fee_amount or (Decimal(str(daily_fee)) * Decimal(overdue_days)).quantize(Decimal('0.01'))

            pending_fee = StorageFee.objects.filter(parcel=parcel, status='pending').order_by('-created_at').first()
            if pending_fee:
                pending_fee.fee_amount = fee_amount
                pending_fee.days_overdue = overdue_days
                pending_fee.currency = currency
                pending_fee.save(update_fields=['fee_amount', 'days_overdue', 'currency'])
                updated_count += 1
            else:
                StorageFee.objects.create(
                    parcel=parcel,
                    fee_amount=fee_amount,
                    currency=currency,
                    days_overdue=overdue_days,
                    status='pending',
                )
                created_count += 1

        self.message_user(
            request,
            (
                f'Storage fee applied from shipment form. Created: {created_count}, '
                f'Updated: {updated_count}. '
                f"Mode: {'manual amount override' if manual_fee_amount else 'auto from Service Charges'}"
            ),
        )
    
    @display(
        description='Status',
        ordering='status',
        label={
            'draft': 'info',
            'declaration_pending': 'warning',
            'pending_payment': 'warning',
            'packing': 'info',
            'dispatched': 'success',
            'in_transit': 'info',
            'customs': 'warning',
            'out_for_delivery': 'success',
            'delivered': 'success',
            'returned': 'info',
            'cancelled': 'danger',
        }
    )
    def status_badge(self, obj):
        return obj.status
    
    @display(
        description='Payment',
        ordering='payment_status',
        label={
            'unpaid': 'danger',
            'partial': 'warning',
            'paid': 'success',
            'refunded': 'info',
        }
    )
    def payment_badge(self, obj):
        return obj.payment_status
    
    def item_count_display(self, obj):
        count = obj.items.count()
        return format_html('<strong>{}</strong> item{}', count, 's' if count != 1 else '')
    item_count_display.short_description = 'Items'
    
    @admin.action(description='✅ Approve Declaration')
    def approve_declaration(self, request, queryset):
        approved = 0
        skipped = 0
        for shipment in queryset.filter(status='declaration_pending'):
            if shipment.approve_declaration():
                approved += 1
            else:
                skipped += 1
        message = f'{approved} shipment(s) approved.'
        if skipped:
            message += f' {skipped} skipped — set Shipping Cost first.'
        self.message_user(request, message)

    @admin.action(description='📦 Mark as Packing')
    def mark_packing(self, request, queryset):
        queryset.update(status='packing')
    
    @admin.action(description='🚀 Mark as Dispatched')
    def mark_dispatched(self, request, queryset):
        from django.utils import timezone
        queryset.update(status='dispatched', dispatched_at=timezone.now())
    
    @admin.action(description='✅ Mark as Delivered')
    def mark_delivered(self, request, queryset):
        from django.utils import timezone
        queryset.update(status='delivered', delivered_at=timezone.now())

    @admin.action(description='➕ Add Storage Fee (Pending)')
    def add_storage_fees(self, request, queryset):
        daily_fee = _get_daily_storage_fee_amount()
        created_count = 0
        skipped_count = 0

        for shipment in queryset.prefetch_related('items__parcel'):
            for item in shipment.items.all():
                parcel = item.parcel
                if not parcel:
                    skipped_count += 1
                    continue

                if parcel.storage_days <= 30:
                    skipped_count += 1
                    continue

                exists = StorageFee.objects.filter(parcel=parcel, status='pending').exists()
                if exists:
                    skipped_count += 1
                    continue

                overdue_days = max(parcel.storage_days - 30, 1)
                fee_amount = (Decimal(str(daily_fee)) * Decimal(overdue_days)).quantize(Decimal('0.01'))

                StorageFee.objects.create(
                    parcel=parcel,
                    fee_amount=fee_amount,
                    currency=shipment.currency or 'INR',
                    days_overdue=overdue_days,
                    status='pending',
                )
                created_count += 1

        self.message_user(
            request,
            f'Created {created_count} storage fee(s). Skipped {skipped_count} parcel(s) with existing pending fee.',
        )


# Proxy Model for Declaration Approvals
class DeclarationPendingShipment(Shipment):
    """Proxy model to show only shipments pending declaration approval."""
    class Meta:
        proxy = True
        verbose_name = "Declaration Approval"
        verbose_name_plural = "📄 Declaration Approvals"


@admin.register(DeclarationPendingShipment)
class DeclarationApprovalAdmin(ModelAdmin):
    """Admin view specifically for approving declarations."""
    list_display = ['display_id', 'user', 'recipient_name', 'city', 'country', 'declaration_link', 'created_at']
    list_filter = ['created_at']
    search_fields = ['display_id', 'user__email', 'recipient_name']
    readonly_fields = ['display_id', 'user', 'shipment_type', 'recipient_name', 'address_line1', 'address_line2', 
                       'city', 'state', 'postal_code', 'country', 'recipient_phone', 'recipient_email',
                       'created_at', 'declaration_document']
    actions = ['approve_declaration']
    
    def get_queryset(self, request):
        """Only show shipments with declaration_pending status."""
        return super().get_queryset(request).filter(status='declaration_pending')
    
    def declaration_link(self, obj):
        """Show link to view the declaration document."""
        doc = obj.documents.filter(document_type='customs').first()
        if doc and doc.document_url:
            # Generate signed URL for private bucket
            from apps.locker.utils import get_signed_shipment_doc_url
            try:
                signed_url = get_signed_shipment_doc_url(doc.document_url)
                return format_html('<a href="{}" target="_blank" style="color: #10B981; font-weight: 600;">📄 View Declaration</a>', signed_url)
            except:
                return mark_safe('<span style="color: #6B7280;">Document unavailable</span>')
        return mark_safe('<span style="color: #EF4444;">No document</span>')
    declaration_link.short_description = "Declaration Form"
    
    def declaration_document(self, obj):
        """Show declaration document in detail view."""
        doc = obj.documents.filter(document_type='customs').first()
        if doc and doc.document_url:
            from apps.locker.utils import get_signed_shipment_doc_url
            try:
                signed_url = get_signed_shipment_doc_url(doc.document_url)
                return format_html(
                    '<div style="padding: 1rem; background: #F0FDF4; border-radius: 8px; margin-bottom: 1rem;">'
                    '<a href="{}" target="_blank" style="color: #059669; font-weight: 600; text-decoration: none;">'
                    '📄 Click to View/Download Declaration Form</a></div>',
                    signed_url
                )
            except Exception as e:
                return format_html('<span style="color: #EF4444;">Error loading document: {}</span>', str(e))
        return mark_safe('<span style="color: #EF4444;">No declaration document uploaded</span>')
    declaration_document.short_description = "Declaration Document"
    
    fieldsets = (
        ('📄 Declaration Document', {
            'fields': ('declaration_document',),
            'description': 'Review the uploaded declaration form below before approving.'
        }),
        ('💰 Pricing', {
            'fields': ('shipping_cost', 'currency'),
            'description': 'Set the shipping cost before approving — the shipment moves to '
                            'Pending Payment (or straight to Packing if already paid) only once a '
                            'price is set. Approving with no price set will skip that shipment.'
        }),
        ('Shipment Info', {
            'fields': ('display_id', 'user', 'shipment_type', 'created_at')
        }),
        ('Recipient Details', {
            'fields': ('recipient_name', 'recipient_phone', 'recipient_email',
                      'address_line1', 'address_line2', 'city', 'state', 'postal_code', 'country')
        }),
    )
    
    @admin.action(description='✅ Approve Declaration')
    def approve_declaration(self, request, queryset):
        approved = 0
        skipped = 0
        for shipment in queryset:
            if shipment.approve_declaration():
                approved += 1
            else:
                skipped += 1
        message = f'{approved} shipment(s) approved.'
        if skipped:
            message += f' {skipped} skipped — set Shipping Cost first (open the shipment and fill in Shipping Cost before approving).'
        self.message_user(request, message)

    def has_add_permission(self, request):
        return False  # Can't add from this view
    
    def has_delete_permission(self, request, obj=None):
        return False  # Can't delete from this view


@admin.register(ShipmentItem)
class ShipmentItemAdmin(ModelAdmin):
    list_display = ['parcel', 'shipment', 'added_at']
    raw_id_fields = ['shipment', 'parcel']


@admin.register(ShipmentDocument)
class ShipmentDocumentAdmin(ModelAdmin):
    form = ShipmentDocumentForm
    list_display = ['shipment', 'document_type', 'document_link', 'uploaded_at']
    list_filter = ['document_type']
    raw_id_fields = ['shipment']
    readonly_fields = ['uploaded_at', 'document_link']
    fields = ['shipment', 'document_file', 'document_type', 'document_url', 'document_link', 'uploaded_at']
    
    def document_link(self, obj):
        if obj.document_url:
            from apps.locker.utils import get_signed_shipment_doc_url
            try:
                signed_url = get_signed_shipment_doc_url(obj.document_url)
                return format_html('<a href="{}" target="_blank">📄 View</a>', signed_url)
            except:
                return "-"
        return "-"
    document_link.short_description = "View"


@admin.register(TrackingEvent)
class TrackingEventAdmin(ModelAdmin):
    """Admin for viewing all tracking events."""
    list_display = ['shipment', 'status', 'location', 'event_timestamp', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['shipment__tracking_number', 'shipment__display_id', 'status', 'location']
    readonly_fields = ['shipment', 'status', 'description', 'location', 'event_timestamp', 'created_at', 'raw_data']
    ordering = ['-event_timestamp']
    date_hierarchy = 'event_timestamp'
    
    def has_add_permission(self, request):
        return False  # Events are created by sync command only
