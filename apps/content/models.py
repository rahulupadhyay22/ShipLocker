"""Content models for CMS functionality."""

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models


class Announcement(models.Model):
    """System-wide announcements shown to users."""
    
    SEVERITY_CHOICES = [
        ('info', 'Info'),
        ('notice', 'Notice'),
        ('important', 'Important'),
        ('urgent', 'Urgent'),
    ]
    
    TEXT_SIZE_CHOICES = [
        ('small', 'Small'),
        ('normal', 'Normal'),
        ('large', 'Large'),
    ]
    TEXT_BOLD_CHOICES = [
        ('bold', 'Bold'),
        ('normal', 'Normal'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    content = models.TextField()
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='info')
    text_size = models.CharField(max_length=20, choices=TEXT_SIZE_CHOICES, default='normal', help_text="Text size for the announcement content")
    text_bold = models.CharField(max_length=20, choices=TEXT_BOLD_CHOICES, default='normal', help_text="Text bold for the announcement content")
    is_active = models.BooleanField(default=True, db_index=True)
    is_dismissible = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    link_url = models.URLField(max_length=500, blank=True, null=True, help_text="Optional link URL for call-to-action")
    link_text = models.CharField(max_length=100, blank=True, null=True, help_text="Text for link button (e.g., 'Read More')")
    
    class Meta:
        verbose_name = 'Announcement'
        verbose_name_plural = 'Announcements'
        ordering = ['-severity', '-created_at']
    
    def __str__(self):
        return self.title


class StaticPage(models.Model):
    """CMS-managed static pages."""
    
    SLUG_CHOICES = [
        ('prohibited-items', 'Prohibited Items'),
        ('shipping-calculator', 'Shipping Calculator'),
        ('service-charges', 'Service Charges'),
        ('refund-policy', 'Refund Policy'),
        ('terms', 'Terms & Conditions'),
        ('privacy', 'Privacy Policy'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(unique=True, choices=SLUG_CHOICES)
    title = models.CharField(max_length=255)
    content = models.TextField(help_text='HTML content allowed')
    meta_description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Static Page'
        verbose_name_plural = 'Static Pages'
    
    def __str__(self):
        return self.title


class PageSection(models.Model):
    """Sections within a static page."""
    
    ICON_CHOICES = [
        ('warning', 'Warning (Triangle)'),
        ('info', 'Info (Circle)'),
        ('check', 'Checkmark'),
        ('ban', 'Ban/Prohibited'),
        ('shipping', 'Shipping'),
        ('document', 'Document'),
    ]
    
    COLOR_CHOICES = [
        ('red', 'Red (Danger)'),
        ('yellow', 'Yellow (Warning)'),
        ('blue', 'Blue (Info)'),
        ('green', 'Green (Success)'),
        ('gray', 'Gray (Neutral)'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    page = models.ForeignKey(StaticPage, on_delete=models.CASCADE, related_name='sections')
    title = models.CharField(max_length=255)
    content = models.TextField(help_text='HTML content allowed')
    icon = models.CharField(max_length=20, choices=ICON_CHOICES, default='info')
    color = models.CharField(max_length=20, choices=COLOR_CHOICES, default='blue')
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['order', 'created_at']
        verbose_name = 'Page Section'
        verbose_name_plural = 'Page Sections'
        
    def __str__(self):
        return f"{self.page.title} - {self.title}"



# Every code some other part of the software actually looks up by — kept as
# a fixed list (not free text) so a typo in the admin can't silently break a
# lookup (pricing.py/services.py would just get None back, no error raised).
# apps/content/admin.py's ServiceChargeAdminForm renders this as a dropdown.
KNOWN_SERVICE_CHARGE_CODES = [
    ('trunkassist_product_link', 'TrunkAssist – Product Link'),
    ('trunkassist_image_search', 'TrunkAssist – Image Search'),
    ('trunkassist_cart_screenshot', 'TrunkAssist – Cart Screenshot'),
    ('trunkassist_boutique_purchase', 'TrunkAssist – Boutique Purchase'),
    ('trunkassist_local_shop_purchase', 'TrunkAssist – Local Shop Purchase'),
    ('trunkassist_custom_request', 'TrunkAssist – Custom Request'),
    ('consolidation_fee', 'Consolidation Fee'),
    ('return_service_charge', 'Return Service Charge'),
    ('addon_insurance', 'Add-on: Insurance'),
    ('addon_extra_photos', 'Add-on: Extra Photos'),
    ('addon_priority_packing', 'Add-on: Priority Packing'),
    ('addon_gift_wrapping', 'Add-on: Gift Wrapping'),
]


class ServiceCharge(models.Model):
    """Service charges and fees configuration — the single admin-editable
    source of truth for every fee this software charges a user, wherever
    that fee is actually computed (TrunkAssist pricing, the consolidation
    fee, etc.) — those call sites read from here via `code`/`compute()`
    instead of hardcoding numbers, so an admin edit here takes effect
    everywhere without a deploy."""

    CHARGE_TYPE_CHOICES = [
        ('flat', 'Flat'),
        ('percentage', 'Percentage of Value'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(
        max_length=60, unique=True, null=True, blank=True,
        help_text="Stable key other code looks this charge up by (e.g. 'trunkassist_product_link', "
                   "'consolidation_fee'). Leave blank for a purely informational row nothing reads programmatically.",
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    charge_type = models.CharField(max_length=10, choices=CHARGE_TYPE_CHOICES, default='flat')
    percentage_rate = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="e.g. 5.00 for 5%. Only used when Charge Type is Percentage.",
    )
    amount = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="The flat fee (Charge Type = Flat) or the minimum floor amount (Charge Type = Percentage).",
    )
    currency = models.CharField(max_length=3, default='INR')
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Service Charge'
        verbose_name_plural = 'Service Charges'

    def __str__(self):
        return f"{self.name} - {self.currency} {self.amount}"

    def compute(self, product_value=None):
        """Resolves this charge to an actual amount for a given product_value.
        Percentage type: whichever is higher of (product_value * rate) or the
        minimum floor (`amount`) — matches never-charge-less-than-the-minimum
        pricing everywhere in this software. Flat type ignores product_value."""
        if self.charge_type == 'percentage' and self.percentage_rate is not None:
            if product_value is None:
                return self.amount
            return max(Decimal(product_value) * (self.percentage_rate / Decimal('100')), self.amount)
        return self.amount


class AdminLog(models.Model):
    """Audit log for admin actions."""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=255)
    details = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'Admin Log'
        verbose_name_plural = 'Admin Logs'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.user} - {self.action} - {self.created_at}"


class ShippingZone(models.Model):
    """Shipping zones grouping countries."""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, help_text="e.g., Zone A - Americas")
    countries = models.TextField(help_text="Comma-separated country codes, e.g., US,CA,MX")
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    
    class Meta:
        verbose_name = 'Shipping Zone'
        verbose_name_plural = 'Shipping Zones'
        ordering = ['order', 'name']
    
    def __str__(self):
        return self.name
    
    def get_countries_list(self):
        return [c.strip().upper() for c in self.countries.split(',') if c.strip()]


class ShippingRate(models.Model):
    """Shipping rates per zone and weight slab."""
    
    RATE_TYPE_CHOICES = [
        ('fixed', 'Fixed Price'),
        ('per_kg', 'Per KG Rate'),
    ]

    SERVICE_TYPE_CHOICES = [
        ('express', 'Express'),
        ('standard', 'Standard'),
        ('economy', 'Economy'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    zone = models.ForeignKey(ShippingZone, on_delete=models.CASCADE, related_name='rates')
    service_type = models.CharField(max_length=20, choices=SERVICE_TYPE_CHOICES, default='standard', db_index=True)
    min_weight = models.DecimalField(max_digits=6, decimal_places=2, help_text="Minimum weight in KG (inclusive)")
    max_weight = models.DecimalField(max_digits=6, decimal_places=2, help_text="Maximum weight in KG (exclusive)")
    rate_type = models.CharField(max_length=10, choices=RATE_TYPE_CHOICES, default='fixed')
    price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Fixed price OR per-kg rate")
    currency = models.CharField(max_length=3, default='INR')
    delivery_days_min = models.PositiveIntegerField(default=5, help_text="Minimum delivery days")
    delivery_days_max = models.PositiveIntegerField(default=10, help_text="Maximum delivery days")
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Shipping Rate'
        verbose_name_plural = 'Shipping Rates'
        ordering = ['zone', 'service_type', 'min_weight']
    
    def __str__(self):
        if self.rate_type == 'fixed':
            return f"{self.zone.name}: {self.min_weight}-{self.max_weight}kg = ₹{self.price}"
        return f"{self.zone.name}: {self.min_weight}-{self.max_weight}kg = ₹{self.price}/kg"
    
    def calculate_price(self, weight):
        """Calculate price for given weight."""
        if self.rate_type == 'fixed':
            return float(self.price)
        else:
            return float(weight) * float(self.price)
