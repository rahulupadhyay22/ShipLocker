from django.db import models
from django.core.cache import cache


class AppSettings(models.Model):
    """
    Singleton model for all application settings.
    Managed through Django Admin - no need to edit .env files.
    """
    
    # ===========================
    # GENERAL SETTINGS
    # ===========================
    site_name = models.CharField(
        max_length=100,
        default="IndiaBox",
        help_text="Your company/site name"
    )
    support_email = models.EmailField(
        default="support@indiabox.com",
        help_text="Support email address"
    )
    support_phone = models.CharField(
        max_length=20,
        default="+91 123456789",
        help_text="Support phone number (WhatsApp enabled)"
    )
    warehouse_address = models.TextField(
        default="",
        help_text="Full warehouse address shown to users"
    )
    
    # ===========================
    # WHATSAPP CLOUD API
    # ===========================
    whatsapp_enabled = models.BooleanField(
        default=False,
        help_text="Enable/disable WhatsApp notifications"
    )
    whatsapp_api_token = models.CharField(
        max_length=500,
        blank=True,
        help_text="Permanent Access Token from Meta App Dashboard"
    )
    whatsapp_phone_number_id = models.CharField(
        max_length=100,
        blank=True,
        help_text="Phone Number ID from WhatsApp Business"
    )
    whatsapp_business_account_id = models.CharField(
        max_length=100,
        blank=True,
        help_text="WhatsApp Business Account ID (optional)"
    )
    
    # Template Names
    template_parcel_added = models.CharField(max_length=100, default="parcel_added")
    template_parcel_action_required = models.CharField(max_length=100, default="parcel_action_required")
    template_shipment_created = models.CharField(max_length=100, default="shipment_created")
    template_shipment_updated = models.CharField(max_length=100, default="shipment_updated")
    
    # ===========================
    # DHL EXPRESS API
    # ===========================
    dhl_enabled = models.BooleanField(
        default=False,
        help_text="Enable DHL Express shipping"
    )
    dhl_api_key = models.CharField(
        max_length=200,
        blank=True,
        help_text="DHL API Key"
    )
    dhl_api_secret = models.CharField(
        max_length=200,
        blank=True,
        help_text="DHL API Secret"
    )
    dhl_account_number = models.CharField(
        max_length=50,
        blank=True,
        help_text="DHL Account Number"
    )
    dhl_test_mode = models.BooleanField(
        default=True,
        help_text="Use DHL sandbox/test environment"
    )
    
    # ===========================
    # FEDEX API
    # ===========================
    fedex_enabled = models.BooleanField(
        default=False,
        help_text="Enable FedEx shipping"
    )
    fedex_api_key = models.CharField(
        max_length=200,
        blank=True,
        help_text="FedEx API Key"
    )
    fedex_secret_key = models.CharField(
        max_length=200,
        blank=True,
        help_text="FedEx Secret Key"
    )
    fedex_account_number = models.CharField(
        max_length=50,
        blank=True,
        help_text="FedEx Account Number"
    )
    fedex_test_mode = models.BooleanField(
        default=True,
        help_text="Use FedEx sandbox/test environment"
    )
    
    # ===========================
    # ARAMEX API
    # ===========================
    aramex_enabled = models.BooleanField(
        default=False,
        help_text="Enable Aramex shipping"
    )
    aramex_username = models.CharField(
        max_length=100,
        blank=True,
        help_text="Aramex Username"
    )
    aramex_password = models.CharField(
        max_length=200,
        blank=True,
        help_text="Aramex Password"
    )
    aramex_account_number = models.CharField(
        max_length=50,
        blank=True,
        help_text="Aramex Account Number"
    )
    aramex_account_pin = models.CharField(
        max_length=50,
        blank=True,
        help_text="Aramex Account PIN"
    )
    aramex_account_entity = models.CharField(
        max_length=10,
        default="HYD",
        help_text="Aramex Account Entity (e.g., HYD)"
    )
    aramex_test_mode = models.BooleanField(
        default=True,
        help_text="Use Aramex sandbox/test environment"
    )
    
    # ===========================
    # BLUEDART API
    # ===========================
    bluedart_enabled = models.BooleanField(
        default=False,
        help_text="Enable BlueDart shipping"
    )
    bluedart_api_token = models.CharField(
        max_length=200,
        blank=True,
        help_text="BlueDart API Token/License Key"
    )
    bluedart_customer_code = models.CharField(
        max_length=50,
        blank=True,
        help_text="BlueDart Customer Code"
    )
    bluedart_login_id = models.CharField(
        max_length=100,
        blank=True,
        help_text="BlueDart Login ID"
    )
    bluedart_license_key = models.CharField(
        max_length=200,
        blank=True,
        help_text="BlueDart License Key (for SOAP API)"
    )
    bluedart_area_code = models.CharField(
        max_length=10,
        default="HYD",
        help_text="Origin Area Code (e.g., HYD, DEL, BOM)"
    )
    bluedart_test_mode = models.BooleanField(
        default=True,
        help_text="Use BlueDart sandbox/test environment"
    )
    
    # ===========================
    # PAYMENT GATEWAY (Razorpay)
    # ===========================
    razorpay_enabled = models.BooleanField(
        default=False,
        help_text="Enable Razorpay payments"
    )
    razorpay_key_id = models.CharField(
        max_length=100,
        blank=True,
        help_text="Razorpay Key ID"
    )
    razorpay_key_secret = models.CharField(
        max_length=200,
        blank=True,
        help_text="Razorpay Key Secret"
    )
    razorpay_test_mode = models.BooleanField(
        default=True,
        help_text="Use Razorpay test mode"
    )
    
    # ===========================
    # SUPABASE STORAGE
    # ===========================
    supabase_url = models.CharField(
        max_length=200,
        blank=True,
        help_text="Supabase Project URL"
    )
    supabase_anon_key = models.CharField(
        max_length=500,
        blank=True,
        help_text="Supabase Anon/Public Key"
    )
    supabase_service_role_key = models.CharField(
        max_length=500,
        blank=True,
        help_text="Supabase Service Role Key (for server-side operations)"
    )
    
    # ===========================
    # METADATA
    # ===========================
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "App Settings"
        verbose_name_plural = "App Settings"
    
    def __str__(self):
        return f"App Settings (Last updated: {self.updated_at})"
    
    def save(self, *args, **kwargs):
        # Singleton pattern - always use pk=1
        self.pk = 1
        super().save(*args, **kwargs)
        # Clear cache when settings change
        cache.delete('app_settings')
    
    @classmethod
    def get_settings(cls):
        """
        Get the singleton settings instance.
        Uses caching for performance.
        """
        settings = cache.get('app_settings')
        if settings is None:
            settings, created = cls.objects.get_or_create(pk=1)
            cache.set('app_settings', settings, 300)  # Cache 5 minutes
        return settings
    
    @classmethod
    def load(cls):
        """Alias for get_settings()"""
        return cls.get_settings()


# Keep NotificationSettings for backward compatibility but mark as deprecated
class NotificationSettings(models.Model):
    """
    DEPRECATED: Use AppSettings instead.
    Kept for migration compatibility.
    """
    class Meta:
        managed = False  # Don't create table
        verbose_name = "Notification Settings (Deprecated)"
