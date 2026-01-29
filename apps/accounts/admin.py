from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, Locker, KYCDocument


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['email', 'full_name', 'phone', 'is_active', 'is_staff', 'date_joined']
    list_filter = ['is_active', 'is_staff', 'date_joined']
    search_fields = ['email', 'full_name', 'phone']
    ordering = ['-date_joined']
    
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal Info', {'fields': ('full_name', 'phone', 'whatsapp_number', 'whatsapp_verified')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Supabase', {'fields': ('supabase_id',)}),
    )
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2', 'full_name'),
        }),
    )


class LockerInline(admin.StackedInline):
    model = Locker
    can_delete = False
    readonly_fields = ['locker_id', 'created_at']


@admin.register(Locker)
class LockerAdmin(admin.ModelAdmin):
    list_display = ['locker_id', 'user', 'created_at']
    search_fields = ['locker_id', 'user__email']
    readonly_fields = ['locker_id', 'created_at']
    raw_id_fields = ['user']


@admin.register(KYCDocument)
class KYCDocumentAdmin(admin.ModelAdmin):
    list_display = ['user', 'document_type', 'status', 'uploaded_at', 'reviewed_at']
    list_filter = ['document_type', 'status']
    search_fields = ['user__email']
    readonly_fields = ['uploaded_at']
    raw_id_fields = ['user']
    
    actions = ['approve_documents', 'reject_documents']
    
    @admin.action(description='Approve selected documents')
    def approve_documents(self, request, queryset):
        from django.utils import timezone
        queryset.update(status='approved', reviewed_at=timezone.now())
    
    @admin.action(description='Reject selected documents')
    def reject_documents(self, request, queryset):
        from django.utils import timezone
        queryset.update(status='rejected', reviewed_at=timezone.now())
