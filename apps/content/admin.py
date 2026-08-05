from django.contrib import admin
from django.utils.html import format_html
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.decorators import display
from .models import Announcement, StaticPage, ServiceCharge, AdminLog, PageSection, ShippingZone, ShippingRate


# ---- Announcement severity colors ----
SEVERITY_COLORS = {
    'info': ('badge-info', 'ℹ️'),
    'notice': ('badge-notice', '📢'),
    'important': ('badge-important', '⚠️'),
    'urgent': ('badge-urgent', '🚨'),
}


@admin.register(Announcement)
class AnnouncementAdmin(ModelAdmin):
    list_display = ['title', 'severity_badge', 'text_size', 'is_active', 'created_at']
    list_filter = ['severity', 'text_size', 'is_active', 'is_dismissible']
    search_fields = ['title', 'content']
    list_editable = ['is_active', 'text_size']
    date_hierarchy = 'created_at'
    fieldsets = (
        (None, {
            'fields': ('title', 'content', 'severity', 'text_size')
        }),
        ('Link (Optional)', {
            'fields': ('link_url', 'link_text'),
            'classes': ('collapse',),
        }),
        ('Settings', {
            'fields': ('is_active', 'is_dismissible', 'expires_at')
        }),
    )
    
    @display(
        description='Severity',
        ordering='severity',
        label={
            'info': 'info',
            'notice': 'info',
            'important': 'warning',
            'urgent': 'danger',
        }
    )
    def severity_badge(self, obj):
        return obj.severity


class PageSectionInline(StackedInline):
    """Inline editor for page sections."""
    model = PageSection
    extra = 0
    fields = ['title', 'content', 'icon', 'color', 'order', 'is_active']
    ordering = ['order']
    show_change_link = True


@admin.register(PageSection)
class PageSectionAdmin(ModelAdmin):
    list_display = ['title', 'page', 'icon', 'color', 'order', 'is_active']
    list_filter = ['page', 'icon', 'color', 'is_active']
    list_editable = ['order', 'is_active']
    search_fields = ['title', 'content']
    ordering = ['page', 'order']


@admin.register(StaticPage)
class StaticPageAdmin(ModelAdmin):
    list_display = ['title', 'slug', 'section_count', 'is_active', 'updated_at']
    list_filter = ['is_active', 'slug']
    search_fields = ['title', 'content']
    inlines = [PageSectionInline]
    
    def section_count(self, obj):
        count = obj.sections.count()
        return format_html('<strong>{}</strong>', count)
    section_count.short_description = 'Sections'


@admin.register(ServiceCharge)
class ServiceChargeAdmin(ModelAdmin):
    list_display = ['name', 'formatted_amount', 'currency', 'is_active']
    list_filter = ['is_active', 'currency']
    list_editable = ['is_active']
    
    def formatted_amount(self, obj):
        symbol = '₹' if obj.currency == 'INR' else obj.currency
        return format_html('<strong>{} {}</strong>', symbol, obj.amount)
    formatted_amount.short_description = 'Amount'
    formatted_amount.admin_order_field = 'amount'


@admin.register(AdminLog)
class AdminLogAdmin(ModelAdmin):
    list_display = ['user', 'action', 'ip_address', 'created_at']
    list_filter = ['created_at']
    search_fields = ['user__email', 'action', 'details']
    readonly_fields = ['id', 'user', 'action', 'details', 'ip_address', 'created_at']
    date_hierarchy = 'created_at'
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False


class ShippingRateInline(TabularInline):
    """Inline editor for shipping rates within a zone."""
    model = ShippingRate
    extra = 1
    fields = ['service_type', 'min_weight', 'max_weight', 'rate_type', 'price', 'delivery_days_min', 'delivery_days_max', 'is_active']


@admin.register(ShippingZone)
class ShippingZoneAdmin(ModelAdmin):
    list_display = ['name', 'country_count', 'countries', 'rate_count', 'is_active', 'order']
    list_filter = ['is_active']
    list_editable = ['is_active', 'order']
    search_fields = ['name', 'countries']
    inlines = [ShippingRateInline]
    
    def rate_count(self, obj):
        count = obj.rates.count()
        return format_html('<strong>{}</strong>', count)
    rate_count.short_description = 'Rates'
    
    def country_count(self, obj):
        count = len(obj.get_countries_list())
        return format_html('<span class="badge-status badge-info">{} countries</span>', count)
    country_count.short_description = 'Countries'


@admin.register(ShippingRate)
class ShippingRateAdmin(ModelAdmin):
    list_display = ['zone', 'service_type', 'weight_range', 'rate_type', 'formatted_price', 'delivery_range', 'is_active']
    list_filter = ['zone', 'service_type', 'rate_type', 'is_active']
    list_editable = ['is_active']
    ordering = ['zone', 'service_type', 'min_weight']
    
    def weight_range(self, obj):
        return format_html('{}–{} kg', obj.min_weight, obj.max_weight)
    weight_range.short_description = 'Weight Range'
    weight_range.admin_order_field = 'min_weight'
    
    def formatted_price(self, obj):
        prefix = 'Per kg' if obj.rate_type == 'per_kg' else 'Fixed'
        return format_html('<strong>₹{}</strong> <small>({})</small>', obj.price, prefix)
    formatted_price.short_description = 'Price'
    formatted_price.admin_order_field = 'price'
    
    def delivery_range(self, obj):
        return format_html('{}–{} days', obj.delivery_days_min, obj.delivery_days_max)
    delivery_range.short_description = 'Delivery'
