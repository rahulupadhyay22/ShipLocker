from django.contrib import admin
from django.utils.html import format_html
from unfold.admin import ModelAdmin
from unfold.decorators import display
from .models import Payment, BatchCharge, Invoice


PAYMENT_STATUS_COLORS = {
    'pending':            ('badge-pending', '⏳'),
    'authorized':         ('badge-declaration', '🔐'),
    'captured':           ('badge-delivered', '✅'),
    'failed':             ('badge-cancelled', '❌'),
    'refunded':           ('badge-returned', '↩️'),
    'partially_refunded': ('badge-returned', '↩️'),
}

CHARGE_STATUS_COLORS = {
    'pending': ('badge-pending', '⏳'),
    'paid':    ('badge-delivered', '✅'),
    'waived':  ('badge-info', 'ℹ️'),
}


@admin.register(Payment)
class PaymentAdmin(ModelAdmin):
    list_display = [
        'display_id', 'user', 'shipment', 'formatted_amount',
        'payment_method', 'status_badge', 'created_at'
    ]
    list_filter = ['status', 'payment_method', 'currency', 'created_at']
    search_fields = [
        'display_id', 'user__email',
        'razorpay_order_id', 'razorpay_payment_id',
        'shipment__display_id'
    ]
    readonly_fields = [
        'display_id', 'created_at', 'updated_at', 'paid_at'
    ]
    raw_id_fields = ['user', 'shipment']
    date_hierarchy = 'created_at'
    list_per_page = 25

    fieldsets = (
        ('Payment Info', {
            'fields': ('user', 'shipment', 'amount', 'currency', 'payment_method', 'status', 'description')
        }),
        ('Razorpay Details', {
            'fields': ('razorpay_order_id', 'razorpay_payment_id', 'razorpay_signature'),
            'classes': ('collapse',),
        }),
        ('Refund', {
            'fields': ('refund_amount', 'refund_id'),
            'classes': ('collapse',),
        }),
        ('Failure', {
            'fields': ('failure_reason',),
            'classes': ('collapse',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'paid_at'),
            'classes': ('collapse',),
        }),
        ('Notes', {
            'fields': ('notes',),
            'classes': ('collapse',),
        }),
    )

    actions = ['mark_captured', 'mark_failed']

    @display(
        description='Status',
        ordering='status',
        label={
            'pending': 'warning',
            'authorized': 'info',
            'captured': 'success',
            'failed': 'danger',
            'refunded': 'info',
            'partially_refunded': 'info',
        }
    )
    def status_badge(self, obj):
        return obj.status

    def formatted_amount(self, obj):
        symbol = '₹' if obj.currency == 'INR' else obj.currency
        return format_html('<strong>{} {}</strong>', symbol, obj.amount)
    formatted_amount.short_description = 'Amount'
    formatted_amount.admin_order_field = 'amount'

    @admin.action(description='✅ Mark as Captured/Paid')
    def mark_captured(self, request, queryset):
        from django.utils import timezone
        queryset.filter(status__in=['pending', 'authorized']).update(
            status='captured', paid_at=timezone.now()
        )

    @admin.action(description='❌ Mark as Failed')
    def mark_failed(self, request, queryset):
        queryset.filter(status__in=['pending', 'authorized']).update(status='failed')


@admin.register(BatchCharge)
class BatchChargeAdmin(ModelAdmin):
    list_display = [
        'batch', 'formatted_amount', 'charge_date',
        'status_badge', 'payment', 'created_at'
    ]
    list_filter = ['status', 'charge_date']
    search_fields = ['batch__locker__locker_id']
    raw_id_fields = ['batch', 'payment']
    readonly_fields = ['created_at', 'paid_at']

    actions = ['waive_charges', 'remove_charges']

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        locker_id = request.GET.get('locker_id')
        if locker_id:
            queryset = queryset.filter(batch__locker__locker_id=locker_id)
        return queryset

    @display(
        description='Status',
        ordering='status',
        label={
            'pending': 'warning',
            'paid': 'success',
            'waived': 'info',
        }
    )
    def status_badge(self, obj):
        return obj.status

    def formatted_amount(self, obj):
        symbol = '₹' if obj.currency == 'INR' else obj.currency
        return format_html('<strong>{} {}</strong>', symbol, obj.amount)
    formatted_amount.short_description = 'Amount'
    formatted_amount.admin_order_field = 'amount'

    @admin.action(description='ℹ️ Waive selected charges')
    def waive_charges(self, request, queryset):
        queryset.filter(status='pending').update(
            status='waived', waived_reason='Waived by admin'
        )

    @admin.action(description='🗑️ Remove selected batch charges')
    def remove_charges(self, request, queryset):
        deleted_count, _ = queryset.delete()
        self.message_user(request, f'Removed {deleted_count} batch charge record(s).')


@admin.register(Invoice)
class InvoiceAdmin(ModelAdmin):
    list_display = ['invoice_number', 'shipment', 'customer_name', 'invoice_date', 'total_amount', 'is_zero_rated']
    list_filter = ['is_zero_rated', 'invoice_date']
    search_fields = ['invoice_number', 'shipment__display_id', 'customer_name', 'customer_email']
    raw_id_fields = ['shipment']
    readonly_fields = [
        'shipment', 'invoice_number', 'invoice_date',
        'customer_name', 'customer_email', 'billing_address', 'customer_gstin',
        'payment_reference', 'payment_method', 'amount_paid',
        'shipping_amount', 'storage_fee_amount', 'consolidation_fee_amount', 'taxable_amount',
        'is_zero_rated', 'gst_rate', 'cgst_amount', 'sgst_amount', 'igst_amount', 'total_amount',
        'pdf_document_url', 'created_at',
    ]
    date_hierarchy = 'invoice_date'
    list_per_page = 25

    def download_link(self, obj):
        if not obj.pdf_document_url:
            return '-'
        from apps.locker.utils import get_signed_shipment_doc_url
        try:
            signed_url = get_signed_shipment_doc_url(obj.pdf_document_url)
            return format_html('<a href="{}" target="_blank">📄 Download PDF</a>', signed_url)
        except Exception:
            return 'Unavailable'
    download_link.short_description = 'PDF'

    fieldsets = (
        ('Invoice', {
            'fields': ('shipment', 'invoice_number', 'invoice_date', 'download_link')
        }),
        ('Customer', {
            'fields': ('customer_name', 'customer_email', 'billing_address', 'customer_gstin')
        }),
        ('Payment', {
            'fields': ('payment_reference', 'payment_method', 'amount_paid')
        }),
        ('Charges', {
            'fields': ('shipping_amount', 'storage_fee_amount', 'consolidation_fee_amount', 'taxable_amount')
        }),
        ('GST', {
            'fields': ('is_zero_rated', 'gst_rate', 'cgst_amount', 'sgst_amount', 'igst_amount', 'total_amount')
        }),
    )
    readonly_fields = readonly_fields + ['download_link']

    def has_add_permission(self, request):
        return False  # Invoices are only created by InvoiceService

    def has_delete_permission(self, request, obj=None):
        return False  # PROTECT on the FK already blocks this; admin shouldn't offer it either
