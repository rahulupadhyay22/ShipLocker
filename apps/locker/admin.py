from django.contrib import admin
from django.utils.html import format_html
from django import forms
from .models import Parcel, ParcelImage, ReturnRequest, DiscardRequest


# ---- Status color maps ----
PARCEL_STATUS_COLORS = {
    'pending': ('badge-pending', '⏳'),
    'action_required': ('badge-action-required', '⚠️'),
    'approved': ('badge-approved', '✅'),
    'shipped': ('badge-shipped', '📦'),
    'returned': ('badge-returned', '↩️'),
    'discarded': ('badge-discarded', '🗑️'),
}

REQUEST_STATUS_COLORS = {
    'requested': ('badge-requested', '⏳'),
    'approved': ('badge-approved', '✅'),
    'processing': ('badge-processing', '🔄'),
    'completed': ('badge-completed', '✅'),
    'confirmed': ('badge-confirmed', '✅'),
    'discarded': ('badge-discarded', '🗑️'),
    'rejected': ('badge-cancelled', '❌'),
}


class ParcelImageForm(forms.ModelForm):
    """Custom form for ParcelImage with file upload."""
    image_file = forms.ImageField(required=False, label='Upload Image')
    
    class Meta:
        model = ParcelImage
        fields = ['parcel', 'is_primary', 'caption', 'image_path']
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        
        # Handle file upload
        image_file = self.cleaned_data.get('image_file')
        if image_file:
            from .utils import upload_parcel_image, get_user_locker_id
            
            # Get locker_id from parcel
            locker_id = instance.parcel.locker.locker_id
            parcel_id = instance.parcel.display_id
            
            # Upload to Supabase
            file_path = upload_parcel_image(
                file=image_file,
                locker_id=locker_id,
                parcel_display_id=parcel_id,
                is_primary=instance.is_primary
            )
            instance.image_path = file_path
        
        if commit:
            instance.save()
        return instance


class ParcelImageInline(admin.TabularInline):
    model = ParcelImage
    form = ParcelImageForm
    extra = 1
    readonly_fields = ['uploaded_at', 'image_preview']
    fields = ['image_file', 'image_path', 'is_primary', 'caption', 'image_preview', 'uploaded_at']
    
    def image_preview(self, obj):
        if obj.image_path:
            url = obj.image_url
            if url:
                return format_html('<a href="{}" target="_blank"><img src="{}" style="max-height: 80px; border-radius: 4px;"/></a>', url, url)
        return '-'
    image_preview.short_description = 'Preview'


@admin.register(Parcel)
class ParcelAdmin(admin.ModelAdmin):
    list_display = ['display_id', 'locker', 'user_email', 'status_badge', 'item_name', 'weight_kg', 'storage_info', 'received_at']
    list_filter = ['status', 'category', 'received_at']
    search_fields = ['display_id', 'locker__locker_id', 'locker__user__email', 'item_name']
    readonly_fields = ['display_id', 'created_at', 'updated_at', 'received_at']
    autocomplete_fields = ['locker']
    inlines = [ParcelImageInline]
    date_hierarchy = 'received_at'
    list_per_page = 25
    
    fieldsets = (
        ('Locker Info', {
            'fields': ('locker', 'status')
        }),
        ('Inspection', {
            'fields': ('weight_kg', 'inspection_notes')
        }),
        ('📐 Dimensions (for volumetric weight)', {
            'fields': ('length_cm', 'width_cm', 'height_cm'),
            'classes': ('collapse',),
            'description': 'Enter box dimensions in cm. Volumetric weight = (L × W × H) / 5000'
        }),
        ('Declaration', {
            'fields': ('item_name', 'item_price', 'item_currency', 'category', 'customs_description', 'invoice_url')
        }),
        ('Timestamps', {
            'fields': ('received_at', 'approved_at', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    actions = ['mark_action_required', 'mark_approved', 'mark_pending']

    def get_queryset(self, request):
        """Optimize admin list queries with select_related.

        Prevents N+1 queries for user_email and storage_info columns.
        Without this: 51 queries for 25 rows → with this: 2 queries.
        """
        return super().get_queryset(request).select_related('locker', 'locker__user')

    def status_badge(self, obj):
        css_class, icon = PARCEL_STATUS_COLORS.get(obj.status, ('badge-draft', '❓'))
        label = obj.get_status_display()
        return format_html(
            '<span class="badge-status {}">{} {}</span>',
            css_class, icon, label
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'
    
    def user_email(self, obj):
        return obj.locker.user.email
    user_email.short_description = 'User'
    user_email.admin_order_field = 'locker__user__email'
    
    def storage_info(self, obj):
        days = obj.storage_days
        if days is None:
            return '-'
        remaining = obj.days_remaining_free
        if remaining is not None and remaining <= 0:
            return format_html('<span style="color:#EF4444;font-weight:600;">{}d (overdue)</span>', days)
        elif remaining is not None and remaining <= 5:
            return format_html('<span style="color:#F59E0B;font-weight:600;">{}d ({}d left)</span>', days, remaining)
        return format_html('{}d', days)
    storage_info.short_description = 'Storage'
    
    @admin.action(description='⏳ Mark as Pending')
    def mark_pending(self, request, queryset):
        queryset.update(status='pending')
    
    @admin.action(description='⚠️ Mark as Action Required')
    def mark_action_required(self, request, queryset):
        queryset.update(status='action_required')
    
    @admin.action(description='✅ Mark as Approved')
    def mark_approved(self, request, queryset):
        from django.utils import timezone
        queryset.update(status='approved', approved_at=timezone.now())


@admin.register(ParcelImage)
class ParcelImageAdmin(admin.ModelAdmin):
    form = ParcelImageForm
    list_display = ['parcel', 'is_primary', 'image_preview', 'uploaded_at']
    list_filter = ['is_primary']
    search_fields = ['parcel__locker__locker_id']
    raw_id_fields = ['parcel']
    readonly_fields = ['uploaded_at', 'image_preview']
    fields = ['parcel', 'image_file', 'image_path', 'is_primary', 'caption', 'image_preview', 'uploaded_at']
    
    def image_preview(self, obj):
        if obj.image_path:
            url = obj.image_url
            if url:
                return format_html('<a href="{}" target="_blank"><img src="{}" style="max-height: 100px; border-radius: 8px;"/></a>', url, url)
        return '-'
    image_preview.short_description = 'Preview'


@admin.register(ReturnRequest)
class ReturnRequestAdmin(admin.ModelAdmin):
    list_display = ['parcel', 'status_badge', 'reason_preview', 'requested_at', 'processed_at']
    list_filter = ['status']
    search_fields = ['parcel__locker__locker_id', 'parcel__display_id']
    raw_id_fields = ['parcel']
    readonly_fields = ['requested_at']
    date_hierarchy = 'requested_at'
    
    actions = ['approve_requests', 'complete_requests']
    
    def status_badge(self, obj):
        css_class, icon = REQUEST_STATUS_COLORS.get(obj.status, ('badge-draft', '❓'))
        label = obj.get_status_display()
        return format_html(
            '<span class="badge-status {}">{} {}</span>',
            css_class, icon, label
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'
    
    def reason_preview(self, obj):
        reason = getattr(obj, 'reason', '') or ''
        if len(reason) > 60:
            return reason[:60] + '...'
        return reason or '-'
    reason_preview.short_description = 'Reason'
    
    @admin.action(description='✅ Approve selected requests')
    def approve_requests(self, request, queryset):
        queryset.filter(status='requested').update(status='approved')
    
    @admin.action(description='✅ Mark as Completed')
    def complete_requests(self, request, queryset):
        from django.utils import timezone
        queryset.update(status='completed', completed_at=timezone.now())


@admin.register(DiscardRequest)
class DiscardRequestAdmin(admin.ModelAdmin):
    list_display = ['parcel', 'status_badge', 'requested_at', 'discarded_at']
    list_filter = ['status']
    search_fields = ['parcel__locker__locker_id', 'parcel__display_id']
    raw_id_fields = ['parcel']
    readonly_fields = ['requested_at']
    date_hierarchy = 'requested_at'
    
    actions = ['confirm_requests', 'mark_discarded']
    
    def status_badge(self, obj):
        css_class, icon = REQUEST_STATUS_COLORS.get(obj.status, ('badge-draft', '❓'))
        label = obj.get_status_display()
        return format_html(
            '<span class="badge-status {}">{} {}</span>',
            css_class, icon, label
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'
    
    @admin.action(description='✅ Confirm selected requests')
    def confirm_requests(self, request, queryset):
        queryset.filter(status='requested').update(status='confirmed')
    
    @admin.action(description='🗑️ Mark as Discarded')
    def mark_discarded(self, request, queryset):
        from django.utils import timezone
        queryset.update(status='discarded', discarded_at=timezone.now())
