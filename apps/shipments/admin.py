import logging

from django.contrib import admin
from django.utils.html import format_html, mark_safe
from django import forms
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display
from .models import Shipment, ShipmentItem, ShipmentDocument, TrackingEvent

logger = logging.getLogger('security')


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

    def has_changed(self):
        # The blank "extra" inline row always submits a document_type (it's a
        # required <select> with no empty option, so the browser sends the
        # first choice by default) even when nothing was actually filled in.
        # Without this override, has_changed() sees that implicit value as a
        # change and the formset saves a new empty ShipmentDocument on every
        # admin save. Only count it as changed if a file or URL was given.
        has_file = bool(self.files.get(self.add_prefix('document_file')))
        has_url = bool(self.data.get(self.add_prefix('document_url')))
        if not has_file and not has_url:
            return False
        return super().has_changed()

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
                'total_weight_kg', 'shipping_cost', 'consolidation_fee', 'currency', 'estimated_delivery_date',
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
        'generate_invoice'
    ]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

        if obj.status == 'declaration_pending' and obj.tracking_number and obj.total_weight_kg:
            if obj.approve_declaration():
                self.message_user(request, 'Declaration auto-approved: tracking number and weight are set.')


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

    def _skip_unpaid(self, request, queryset):
        """Shared precondition for the fulfillment actions below — none of
        them should ever apply to a shipment that hasn't been paid for yet,
        unlike approve_declaration these had no filter at all."""
        payable, unpaid = [], 0
        for shipment in queryset:
            if shipment.payment_status == 'paid':
                payable.append(shipment)
            else:
                unpaid += 1
        if unpaid:
            self.message_user(
                request, f'{unpaid} shipment(s) skipped — not paid yet.', level='warning',
            )
        return payable

    @admin.action(description='📦 Mark as Packing')
    def mark_packing(self, request, queryset):
        for shipment in self._skip_unpaid(request, queryset):
            shipment.status = 'packing'
            shipment.save(update_fields=['status', 'updated_at'])

    @admin.action(description='🚀 Mark as Dispatched')
    def mark_dispatched(self, request, queryset):
        from django.utils import timezone
        now = timezone.now()
        for shipment in self._skip_unpaid(request, queryset):
            shipment.status = 'dispatched'
            shipment.dispatched_at = now
            shipment.save(update_fields=['status', 'dispatched_at', 'updated_at'])

    @admin.action(description='✅ Mark as Delivered')
    def mark_delivered(self, request, queryset):
        from django.utils import timezone
        now = timezone.now()
        for shipment in self._skip_unpaid(request, queryset):
            shipment.status = 'delivered'
            shipment.delivered_at = now
            shipment.save(update_fields=['status', 'delivered_at', 'updated_at'])

    @admin.action(description='🧾 Generate Invoice')
    def generate_invoice(self, request, queryset):
        from apps.payments.services import InvoiceService

        generated = 0
        skipped = 0
        for shipment in queryset:
            if shipment.payment_status != 'paid':
                skipped += 1
                continue
            try:
                InvoiceService.generate_for_shipment(shipment, paid_at=shipment.paid_at)
                generated += 1
            except Exception:
                logger.exception(f"Manual invoice generation failed for shipment {shipment.pk}")
                skipped += 1

        message = f'{generated} invoice(s) generated (or already existed).'
        if skipped:
            message += f' {skipped} skipped — either not paid yet, or generation failed (check logs).'
        self.message_user(request, message)


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
            except Exception as e:
                logger.error(f'declaration_link failed for shipment {obj.pk}: {e}')
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
                logger.error(f'declaration_document failed for shipment {obj.pk}: {e}')
                return mark_safe('<span style="color: #EF4444;">Error loading document</span>')
        return mark_safe('<span style="color: #EF4444;">No declaration document uploaded</span>')
    declaration_document.short_description = "Declaration Document"
    
    fieldsets = (
        ('📄 Declaration Document', {
            'fields': ('declaration_document',),
            'description': 'Review the uploaded declaration form below before approving.'
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
            message += f' {skipped} skipped — not in Declaration Pending status.'
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
