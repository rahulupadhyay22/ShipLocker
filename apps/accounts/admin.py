from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html
from .models import User, Locker, KYCDocument, SavedAddress


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['email', 'full_name', 'phone', 'locker_id_display', 'is_active', 'is_staff', 'date_joined']
    list_filter = ['is_active', 'is_staff', 'date_joined']
    search_fields = ['email', 'full_name', 'phone']
    ordering = ['-date_joined']
    date_hierarchy = 'date_joined'
    
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal Info', {'fields': ('full_name', 'phone', 'whatsapp_number', 'whatsapp_verified', 'email_verified')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Supabase', {'fields': ('supabase_id',)}),
    )
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2', 'full_name'),
        }),
    )
    
    def locker_id_display(self, obj):
        try:
            return format_html('<code>{}</code>', obj.locker.locker_id)
        except Exception:
            return format_html('<span style="color:#9CA3AF;">No locker</span>')
    locker_id_display.short_description = 'Locker ID'


class LockerInline(admin.StackedInline):
    model = Locker
    can_delete = False
    readonly_fields = ['locker_id', 'created_at']


@admin.register(Locker)
class LockerAdmin(admin.ModelAdmin):
    list_display = ['locker_id', 'user', 'parcel_count', 'created_at']
    search_fields = ['locker_id', 'user__email']
    readonly_fields = ['locker_id', 'created_at']
    raw_id_fields = ['user']
    
    def parcel_count(self, obj):
        count = obj.parcels.count()
        return format_html('<strong>{}</strong>', count)
    parcel_count.short_description = 'Parcels'


KYC_STATUS_COLORS = {
    'pending': ('badge-kyc-pending', '⏳'),
    'approved': ('badge-kyc-approved', '✅'),
    'rejected': ('badge-kyc-rejected', '❌'),
}


@admin.register(KYCDocument)
class KYCDocumentAdmin(admin.ModelAdmin):
    list_display = ['user', 'document_type', 'status_badge', 'uploaded_at', 'reviewed_at']
    list_filter = ['document_type', 'status']
    search_fields = ['user__email']
    readonly_fields = ['uploaded_at']
    raw_id_fields = ['user']
    date_hierarchy = 'uploaded_at'
    
    actions = ['approve_documents', 'reject_documents']
    
    def status_badge(self, obj):
        css_class, icon = KYC_STATUS_COLORS.get(obj.status, ('badge-draft', '❓'))
        label = obj.get_status_display()
        return format_html(
            '<span class="badge-status {}">{} {}</span>',
            css_class, icon, label
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'
    
    @admin.action(description='✅ Approve selected documents')
    def approve_documents(self, request, queryset):
        from django.utils import timezone
        queryset.update(status='approved', reviewed_at=timezone.now())
    
    @admin.action(description='❌ Reject selected documents')
    def reject_documents(self, request, queryset):
        from django.utils import timezone
        queryset.update(status='rejected', reviewed_at=timezone.now())


@admin.register(SavedAddress)
class SavedAddressAdmin(admin.ModelAdmin):
    list_display = ['user', 'label', 'recipient_name', 'city', 'country', 'is_default', 'updated_at']
    list_filter = ['country', 'is_default']
    search_fields = ['user__email', 'recipient_name', 'city', 'label']
    raw_id_fields = ['user']
    list_editable = ['is_default']

