from django.contrib import admin
from django.utils.html import format_html
from .models import AppSettings


@admin.register(AppSettings)
class AppSettingsAdmin(admin.ModelAdmin):
    """
    Admin interface for managing all app settings.
    Organized into logical sections with help text.
    """
    
    fieldsets = (
        ('🏢 General Settings', {
            'fields': (
                'site_name',
                'support_email',
                'support_phone',
                'warehouse_address',
            ),
            'description': 'Basic site configuration'
        }),
        ('💬 WhatsApp Cloud API', {
            'fields': (
                'whatsapp_enabled',
                'whatsapp_api_token',
                'whatsapp_phone_number_id',
                'whatsapp_business_account_id',
            ),
            'description': 'Configure WhatsApp notifications via Meta Cloud API. Get credentials from developers.facebook.com',
            'classes': ('collapse',),  # Collapsible by default
        }),
        ('📱 WhatsApp Message Templates', {
            'fields': (
                'template_parcel_added',
                'template_parcel_action_required',
                'template_shipment_created',
                'template_shipment_updated',
            ),
            'description': 'Template names must match approved templates in Meta Business Manager',
            'classes': ('collapse',),
        }),
        ('📦 DHL Express API', {
            'fields': (
                'dhl_enabled',
                'dhl_test_mode',
                'dhl_api_key',
                'dhl_api_secret',
                'dhl_account_number',
            ),
            'description': 'DHL Express shipping integration. Get API credentials from developer.dhl.com',
            'classes': ('collapse',),
        }),
        ('📦 FedEx API', {
            'fields': (
                'fedex_enabled',
                'fedex_test_mode',
                'fedex_api_key',
                'fedex_secret_key',
                'fedex_account_number',
            ),
            'description': 'FedEx shipping integration. Get API credentials from developer.fedex.com',
            'classes': ('collapse',),
        }),
        ('📦 Aramex API', {
            'fields': (
                'aramex_enabled',
                'aramex_test_mode',
                'aramex_username',
                'aramex_password',
                'aramex_account_number',
                'aramex_account_pin',
                'aramex_account_entity',
            ),
            'description': 'Aramex shipping integration',
            'classes': ('collapse',),
        }),
        ('🚚 BlueDart API', {
            'fields': (
                'bluedart_enabled',
                'bluedart_test_mode',
                'bluedart_login_id',
                'bluedart_license_key',
                'bluedart_customer_code',
                'bluedart_api_token',
                'bluedart_area_code',
            ),
            'description': 'BlueDart shipping integration for India domestic. Get credentials from bluedart.com',
            'classes': ('collapse',),
        }),
        ('💳 Razorpay Payment Gateway', {
            'fields': (
                'razorpay_enabled',
                'razorpay_test_mode',
                'razorpay_key_id',
                'razorpay_key_secret',
            ),
            'description': 'Razorpay payment integration. Get credentials from dashboard.razorpay.com',
            'classes': ('collapse',),
        }),
        ('☁️ Supabase Storage', {
            'fields': (
                'supabase_url',
                'supabase_anon_key',
                'supabase_service_role_key',
            ),
            'description': 'Supabase configuration for file storage',
            'classes': ('collapse',),
        }),
        ('ℹ️ Status', {
            'fields': ('updated_at',),
        }),
    )
    
    readonly_fields = ('updated_at',)
    
    def has_add_permission(self, request):
        # Only allow one instance (singleton)
        return not AppSettings.objects.exists()
    
    def has_delete_permission(self, request, obj=None):
        # Don't allow deletion
        return False
    
    def changelist_view(self, request, extra_context=None):
        # Auto-redirect to the single settings instance
        obj, created = AppSettings.objects.get_or_create(pk=1)
        from django.shortcuts import redirect
        return redirect(f'/admin/notifications/appsettings/{obj.pk}/change/')
    
    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # Add password widget for sensitive fields
        sensitive_fields = [
            'whatsapp_api_token',
            'dhl_api_secret',
            'fedex_secret_key',
            'aramex_password',
            'bluedart_license_key',
            'razorpay_key_secret',
            'supabase_service_role_key',
        ]
        for field_name in sensitive_fields:
            if field_name in form.base_fields:
                form.base_fields[field_name].widget.attrs['type'] = 'password'
        return form
    
    def status_indicator(self, obj):
        """Show quick status of enabled integrations."""
        icons = []
        if obj.whatsapp_enabled:
            icons.append('💬')
        if obj.dhl_enabled:
            icons.append('📦 DHL')
        if obj.fedex_enabled:
            icons.append('📦 FedEx')
        if obj.aramex_enabled:
            icons.append('📦 Aramex')
        if obj.bluedart_enabled:
            icons.append('🚚 BlueDart')
        if obj.razorpay_enabled:
            icons.append('💳')
        return format_html(' | '.join(icons) if icons else '<span style="color: #999;">No integrations enabled</span>')
    status_indicator.short_description = 'Active Integrations'
