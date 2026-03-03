from django.contrib import admin
from django.utils.html import format_html
from .models import Payment, StorageFee


PAYMENT_STATUS_COLORS = {
    'pending':            ('badge-pending', '⏳'),
    'authorized':         ('badge-declaration', '🔐'),
    'captured':           ('badge-delivered', '✅'),
    'failed':             ('badge-cancelled', '❌'),
    'refunded':           ('badge-returned', '↩️'),
    'partially_refunded': ('badge-returned', '↩️'),
}

STORAGE_STATUS_COLORS = {
    'pending': ('badge-pending', '⏳'),
    'paid':    ('badge-delivered', '✅'),
    'waived':  ('badge-info', 'ℹ️'),
}


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
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

    def status_badge(self, obj):
        css_class, icon = PAYMENT_STATUS_COLORS.get(obj.status, ('badge-draft', '❓'))
        label = obj.get_status_display()
        return format_html(
            '<span class="badge-status {}">{} {}</span>',
            css_class, icon, label
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'

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


@admin.register(StorageFee)
class StorageFeeAdmin(admin.ModelAdmin):
    list_display = [
        'parcel', 'formatted_fee', 'days_overdue',
        'status_badge', 'payment', 'created_at'
    ]
    list_filter = ['status', 'created_at']
    search_fields = ['parcel__display_id', 'parcel__locker__locker_id']
    raw_id_fields = ['parcel', 'payment']
    readonly_fields = ['created_at', 'paid_at']

    actions = ['waive_fees', 'remove_fees']

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        shipment_id = request.GET.get('shipment_id')
        if shipment_id:
            queryset = queryset.filter(
                parcel__shipment_items__shipment_id=shipment_id
            ).distinct()
        return queryset

    def status_badge(self, obj):
        css_class, icon = STORAGE_STATUS_COLORS.get(obj.status, ('badge-draft', '❓'))
        label = obj.get_status_display()
        return format_html(
            '<span class="badge-status {}">{} {}</span>',
            css_class, icon, label
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'

    def formatted_fee(self, obj):
        symbol = '₹' if obj.currency == 'INR' else obj.currency
        return format_html('<strong>{} {}</strong>', symbol, obj.fee_amount)
    formatted_fee.short_description = 'Fee'
    formatted_fee.admin_order_field = 'fee_amount'

    @admin.action(description='ℹ️ Waive selected fees')
    def waive_fees(self, request, queryset):
        queryset.filter(status='pending').update(
            status='waived', waived_reason='Waived by admin'
        )

    @admin.action(description='🗑️ Remove selected storage fees')
    def remove_fees(self, request, queryset):
        deleted_count, _ = queryset.delete()
        self.message_user(request, f'Removed {deleted_count} storage fee record(s).')
