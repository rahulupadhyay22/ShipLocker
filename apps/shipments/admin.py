from django.contrib import admin
from django.utils.html import format_html, mark_safe
from django import forms
from .models import Shipment, ShipmentItem, ShipmentDocument, TrackingEvent


class ShipmentItemInline(admin.TabularInline):
    model = ShipmentItem
    extra = 1
    raw_id_fields = ['parcel']
    readonly_fields = ['added_at']


class ShipmentDocumentForm(forms.ModelForm):
    """Custom form for ShipmentDocument with file upload."""
    document_file = forms.FileField(required=False, label='Upload Document')
    
    class Meta:
        model = ShipmentDocument
        fields = ['shipment', 'document_type', 'document_url']
    
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


class ShipmentDocumentInline(admin.TabularInline):
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


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ['display_id', 'user', 'shipment_type', 'status', 'carrier', 'tracking_number', 'created_at']
    list_filter = ['status', 'shipment_type', 'carrier', 'created_at']
    search_fields = ['display_id', 'user__email', 'tracking_number', 'recipient_name']
    readonly_fields = ['display_id', 'created_at', 'updated_at']
    raw_id_fields = ['user']
    inlines = [ShipmentItemInline, ShipmentDocumentInline]
    
    fieldsets = (
        ('Shipment Info', {
            'fields': ('user', 'shipment_type', 'status')
        }),
        ('Carrier & Tracking', {
            'fields': ('carrier', 'tracking_number', 'tracking_url')
        }),
        ('Recipient', {
            'fields': ('recipient_name', 'recipient_phone', 'recipient_email',
                      'address_line1', 'address_line2', 'city', 'state', 'postal_code', 'country')
        }),
        ('Weight & Cost', {
            'fields': ('total_weight_kg', 'shipping_cost', 'currency')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'dispatched_at', 'delivered_at'),
            'classes': ('collapse',)
        }),
        ('Notes', {
            'fields': ('admin_notes',),
            'classes': ('collapse',)
        }),
    )
    
    actions = ['approve_declaration', 'mark_dispatched', 'mark_delivered']
    
    @admin.action(description='✅ Approve Declaration (→ Packing)')
    def approve_declaration(self, request, queryset):
        updated = queryset.filter(status='declaration_pending').update(status='packing')
        self.message_user(request, f'{updated} shipment(s) approved and moved to Packing.')
    
    @admin.action(description='Mark as Dispatched')
    def mark_dispatched(self, request, queryset):
        from django.utils import timezone
        queryset.update(status='dispatched', dispatched_at=timezone.now())
    
    @admin.action(description='Mark as Delivered')
    def mark_delivered(self, request, queryset):
        from django.utils import timezone
        queryset.update(status='delivered', delivered_at=timezone.now())


# Proxy Model for Declaration Approvals
class DeclarationPendingShipment(Shipment):
    """Proxy model to show only shipments pending declaration approval."""
    class Meta:
        proxy = True
        verbose_name = "Declaration Approval"
        verbose_name_plural = "📄 Declaration Approvals"


@admin.register(DeclarationPendingShipment)
class DeclarationApprovalAdmin(admin.ModelAdmin):
    """Admin view specifically for approving declarations."""
    list_display = ['shipment_id', 'user', 'recipient_name', 'city', 'country', 'declaration_link', 'created_at']
    list_filter = ['created_at']
    search_fields = ['id', 'user__email', 'recipient_name']
    readonly_fields = ['id', 'user', 'shipment_type', 'recipient_name', 'address_line1', 'address_line2', 
                       'city', 'state', 'postal_code', 'country', 'recipient_phone', 'recipient_email',
                       'created_at', 'declaration_document']
    actions = ['approve_declaration']
    
    def get_queryset(self, request):
        """Only show shipments with declaration_pending status."""
        return super().get_queryset(request).filter(status='declaration_pending')
    
    def shipment_id(self, obj):
        return f"SHP-{str(obj.id)[:8].upper()}"
    shipment_id.short_description = "Shipment ID"
    
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
        ('Shipment Info', {
            'fields': ('id', 'user', 'shipment_type', 'created_at')
        }),
        ('Recipient Details', {
            'fields': ('recipient_name', 'recipient_phone', 'recipient_email',
                      'address_line1', 'address_line2', 'city', 'state', 'postal_code', 'country')
        }),
    )
    
    @admin.action(description='✅ Approve Declaration (→ Packing)')
    def approve_declaration(self, request, queryset):
        updated = queryset.update(status='packing')
        self.message_user(request, f'{updated} shipment(s) approved and moved to Packing.')
    
    def has_add_permission(self, request):
        return False  # Can't add from this view
    
    def has_delete_permission(self, request, obj=None):
        return False  # Can't delete from this view


@admin.register(ShipmentItem)
class ShipmentItemAdmin(admin.ModelAdmin):
    list_display = ['parcel', 'shipment', 'added_at']
    raw_id_fields = ['shipment', 'parcel']


@admin.register(ShipmentDocument)
class ShipmentDocumentAdmin(admin.ModelAdmin):
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


class TrackingEventInline(admin.TabularInline):
    """Inline for viewing tracking events in Shipment admin."""
    model = TrackingEvent
    extra = 0
    readonly_fields = ['status', 'description', 'location', 'event_timestamp', 'created_at']
    ordering = ['-event_timestamp']
    
    def has_add_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TrackingEvent)
class TrackingEventAdmin(admin.ModelAdmin):
    """Admin for viewing all tracking events."""
    list_display = ['shipment', 'status', 'location', 'event_timestamp', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['shipment__tracking_number', 'status', 'location']
    readonly_fields = ['shipment', 'status', 'description', 'location', 'event_timestamp', 'created_at', 'raw_data']
    ordering = ['-event_timestamp']
    
    def has_add_permission(self, request):
        return False  # Events are created by sync command only

