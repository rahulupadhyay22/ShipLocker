from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html
from unfold.admin import ModelAdmin, StackedInline
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from unfold.decorators import display
from .models import User, Locker, KYCDocument, SavedAddress, ConsentRecord


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
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

    def has_delete_permission(self, request, obj=None):
        # Hard-deleting a User cascades away Payment/Shipment/BatchCharge financial
        # records (see apps/accounts/account_deletion.py). The only sanctioned way
        # to remove a user's data is that module's anonymize-in-place flow, reached
        # by the user themselves via AccountDeletionRequestView.
        return False


class LockerInline(StackedInline):
    model = Locker
    can_delete = False
    readonly_fields = ['locker_id', 'created_at']


@admin.register(Locker)
class LockerAdmin(ModelAdmin):
    list_display = ['locker_id', 'user', 'parcel_count', 'created_at']
    search_fields = ['locker_id', 'user__email']
    readonly_fields = ['locker_id', 'created_at']
    raw_id_fields = ['user']

    def parcel_count(self, obj):
        count = obj.parcels.count()
        return format_html('<strong>{}</strong>', count)
    parcel_count.short_description = 'Parcels'

    def save_model(self, request, obj, form, change):
        old_plan_type = None
        if change:
            old_plan_type = Locker.objects.get(pk=obj.pk).plan_type
        super().save_model(request, obj, form, change)
        if change and old_plan_type is not None and old_plan_type != obj.plan_type:
            from apps.locker.services.batch_billing import (
                apply_upgrade, apply_downgrade, get_open_batch, resolve_grace_period,
            )
            from datetime import timedelta
            from django.utils import timezone
            today = timezone.localdate()
            if obj.plan_type == 'paid':
                in_grace = obj.payment_grace_until is not None
                obj.premium_expires_at = today + timedelta(days=365)
                obj.save(update_fields=['premium_expires_at'])
                if in_grace:
                    resolve_grace_period(obj, today, payment_succeeded=True)
                else:
                    apply_upgrade(obj, today, active_batch=get_open_batch(obj))
            else:
                obj.premium_expires_at = None
                obj.save(update_fields=['premium_expires_at'])
                apply_downgrade(obj, today)


KYC_STATUS_COLORS = {
    'pending': ('badge-kyc-pending', '⏳'),
    'approved': ('badge-kyc-approved', '✅'),
    'rejected': ('badge-kyc-rejected', '❌'),
}


@admin.register(KYCDocument)
class KYCDocumentAdmin(ModelAdmin):
    list_display = ['user', 'document_type', 'status_badge', 'uploaded_at', 'reviewed_at']
    list_filter = ['document_type', 'status']
    search_fields = ['user__email']
    readonly_fields = ['uploaded_at']
    raw_id_fields = ['user']
    date_hierarchy = 'uploaded_at'
    
    actions = ['approve_documents', 'reject_documents']
    
    @display(
        description='Status',
        ordering='status',
        label={
            'pending': 'warning',
            'approved': 'success',
            'rejected': 'danger',
        }
    )
    def status_badge(self, obj):
        return obj.status
    
    @admin.action(description='✅ Approve selected documents')
    def approve_documents(self, request, queryset):
        from django.utils import timezone
        queryset.update(status='approved', reviewed_at=timezone.now())
    
    @admin.action(description='❌ Reject selected documents')
    def reject_documents(self, request, queryset):
        from django.utils import timezone
        queryset.update(status='rejected', reviewed_at=timezone.now())


@admin.register(SavedAddress)
class SavedAddressAdmin(ModelAdmin):
    list_display = ['user', 'label', 'recipient_name', 'city', 'country', 'is_default', 'updated_at']
    list_filter = ['country', 'is_default']
    search_fields = ['user__email', 'recipient_name', 'city', 'label']
    raw_id_fields = ['user']
    list_editable = ['is_default']


@admin.register(ConsentRecord)
class ConsentRecordAdmin(ModelAdmin):
    """Read-only audit trail — DPDP consent proof. Nothing here should ever
    be editable or deletable; it exists to be pointed at, not modified."""
    list_display = ['user', 'consent_type', 'policy_version', 'ip_address', 'consented_at']
    list_filter = ['consent_type', 'policy_version']
    search_fields = ['user__email']
    raw_id_fields = ['user']
    date_hierarchy = 'consented_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

