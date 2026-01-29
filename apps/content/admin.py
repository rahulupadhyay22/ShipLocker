from django.contrib import admin
from .models import Announcement, StaticPage, ServiceCharge, AdminLog, PageSection, ShippingZone, ShippingRate


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ['title', 'severity', 'text_size', 'is_active', 'created_at']
    list_filter = ['severity', 'text_size', 'is_active', 'is_dismissible']
    search_fields = ['title', 'content']
    list_editable = ['is_active', 'text_size']
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


class PageSectionInline(admin.StackedInline):
    """Inline editor for page sections."""
    model = PageSection
    extra = 0
    fields = ['title', 'content', 'icon', 'color', 'order', 'is_active']
    ordering = ['order']
    show_change_link = True


@admin.register(PageSection)
class PageSectionAdmin(admin.ModelAdmin):
    list_display = ['title', 'page', 'icon', 'color', 'order', 'is_active']
    list_filter = ['page', 'icon', 'color', 'is_active']
    list_editable = ['order', 'is_active']
    search_fields = ['title', 'content']
    ordering = ['page', 'order']


@admin.register(StaticPage)
class StaticPageAdmin(admin.ModelAdmin):
    list_display = ['title', 'slug', 'section_count', 'is_active', 'updated_at']
    list_filter = ['is_active', 'slug']
    search_fields = ['title', 'content']
    inlines = [PageSectionInline]
    
    def section_count(self, obj):
        return obj.sections.count()
    section_count.short_description = 'Sections'


@admin.register(ServiceCharge)
class ServiceChargeAdmin(admin.ModelAdmin):
    list_display = ['name', 'amount', 'currency', 'is_active']
    list_filter = ['is_active', 'currency']
    list_editable = ['amount', 'is_active']


@admin.register(AdminLog)
class AdminLogAdmin(admin.ModelAdmin):
    list_display = ['user', 'action', 'ip_address', 'created_at']
    list_filter = ['created_at']
    search_fields = ['user__email', 'action', 'details']
    readonly_fields = ['id', 'user', 'action', 'details', 'ip_address', 'created_at']
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False


class ShippingRateInline(admin.TabularInline):
    """Inline editor for shipping rates within a zone."""
    model = ShippingRate
    extra = 1
    fields = ['min_weight', 'max_weight', 'rate_type', 'price', 'delivery_days_min', 'delivery_days_max', 'is_active']


@admin.register(ShippingZone)
class ShippingZoneAdmin(admin.ModelAdmin):
    list_display = ['name', 'countries', 'rate_count', 'is_active', 'order']
    list_filter = ['is_active']
    list_editable = ['is_active', 'order']
    search_fields = ['name', 'countries']
    inlines = [ShippingRateInline]
    
    def rate_count(self, obj):
        return obj.rates.count()
    rate_count.short_description = 'Rates'


@admin.register(ShippingRate)
class ShippingRateAdmin(admin.ModelAdmin):
    list_display = ['zone', 'min_weight', 'max_weight', 'rate_type', 'price', 'delivery_days_min', 'delivery_days_max', 'is_active']
    list_filter = ['zone', 'rate_type', 'is_active']
    list_editable = ['price', 'is_active']
    ordering = ['zone', 'min_weight']
