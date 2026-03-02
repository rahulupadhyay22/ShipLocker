import uuid
from django.db import models, transaction
from apps.accounts.models import Locker


def generate_parcel_id(locker):
    """Generate sequential parcel ID: RB-12345-P001 (race-condition safe)."""
    from apps.locker.models import Parcel
    locker_id = locker.locker_id
    with transaction.atomic():
        last = (
            Parcel.objects
            .select_for_update()
            .filter(locker=locker)
            .order_by('-created_at')
            .first()
        )
        if last and last.display_id:
            try:
                num = int(last.display_id.rsplit('-P', 1)[1]) + 1
            except (ValueError, IndexError):
                num = Parcel.objects.filter(locker=locker).count() + 1
        else:
            num = 1
    return f"{locker_id}-P{num:03d}"


def generate_parcel_image_id(parcel):
    """Generate sequential parcel image ID: RB-12345-P001-I001 (race-condition safe)."""
    from apps.locker.models import ParcelImage
    parcel_id = parcel.display_id
    with transaction.atomic():
        last = (
            ParcelImage.objects
            .select_for_update()
            .filter(parcel=parcel)
            .order_by('-uploaded_at')
            .first()
        )
        if last and last.display_id:
            try:
                num = int(last.display_id.rsplit('-I', 1)[1]) + 1
            except (ValueError, IndexError):
                num = ParcelImage.objects.filter(parcel=parcel).count() + 1
        else:
            num = 1
    return f"{parcel_id}-I{num:03d}"


def generate_return_request_id(parcel):
    """Generate sequential return request ID: RB-12345-P001-R001 (race-condition safe)."""
    from apps.locker.models import ReturnRequest
    parcel_id = parcel.display_id
    with transaction.atomic():
        last = (
            ReturnRequest.objects
            .select_for_update()
            .filter(parcel=parcel)
            .order_by('-requested_at')
            .first()
        )
        if last and last.display_id:
            try:
                num = int(last.display_id.rsplit('-R', 1)[1]) + 1
            except (ValueError, IndexError):
                num = ReturnRequest.objects.filter(parcel=parcel).count() + 1
        else:
            num = 1
    return f"{parcel_id}-R{num:03d}"


def generate_discard_request_id(parcel):
    """Generate sequential discard request ID: RB-12345-P001-D001 (race-condition safe)."""
    from apps.locker.models import DiscardRequest
    parcel_id = parcel.display_id
    with transaction.atomic():
        last = (
            DiscardRequest.objects
            .select_for_update()
            .filter(parcel=parcel)
            .order_by('-requested_at')
            .first()
        )
        if last and last.display_id:
            try:
                num = int(last.display_id.rsplit('-D', 1)[1]) + 1
            except (ValueError, IndexError):
                num = DiscardRequest.objects.filter(parcel=parcel).count() + 1
        else:
            num = 1
    return f"{parcel_id}-D{num:03d}"


class Parcel(models.Model):
    """Parcel received at the warehouse for a user's locker."""
    
    STATUS_CHOICES = [
        ('pending', 'Pending Inspection'),
        ('action_required', 'Action Required'),
        ('approved', 'Approved - Ready to Ship'),
        ('shipped', 'Shipped'),
        ('return_requested', 'Return Requested'),
        ('return_approved', 'Return Approved'),
        ('returned', 'Returned'),
        ('discard_requested', 'Discard Requested'),
        ('discarded', 'Discarded'),
    ]
    
    CATEGORY_CHOICES = [
        ('clothing', 'Clothing & Accessories'),
        ('electronics', 'Electronics'),
        ('cosmetics', 'Cosmetics & Personal Care'),
        ('food', 'Food & Supplements'),
        ('home', 'Home & Kitchen'),
        ('books', 'Books & Stationery'),
        ('jewelry', 'Jewelry & Watches'),
        ('sports', 'Sports & Fitness'),
        ('other', 'Other'),
    ]
    
    # Primary key - keep UUID internally for database
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # User-friendly display ID: RB-12345-P001
    display_id = models.CharField(max_length=50, unique=True, editable=False, db_index=True, null=True, blank=True)
    
    locker = models.ForeignKey(Locker, on_delete=models.CASCADE, related_name='parcels')
    
    # Tracking info (set by admin) - tracking_number is optional
    tracking_number = models.CharField(max_length=100, blank=True, null=True)
    carrier = models.CharField(max_length=100, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    
    # Inspection info (set by admin)
    inspection_notes = models.TextField(blank=True)
    weight_kg = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    
    # Dimensions for volumetric weight calculation (L x W x H in cm)
    length_cm = models.DecimalField(
        max_digits=6, decimal_places=1, null=True, blank=True,
        help_text="Length in cm"
    )
    width_cm = models.DecimalField(
        max_digits=6, decimal_places=1, null=True, blank=True,
        help_text="Width in cm"
    )
    height_cm = models.DecimalField(
        max_digits=6, decimal_places=1, null=True, blank=True,
        help_text="Height in cm"
    )
    
    # Declaration info (set by user)
    item_name = models.CharField(max_length=255, blank=True)
    item_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    item_currency = models.CharField(max_length=3, default='INR')
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, blank=True)
    customs_description = models.TextField(blank=True)
    invoice_url = models.CharField(max_length=500, blank=True)  # Supabase Storage path
    
    # Status
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='pending', db_index=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Parcel'
        verbose_name_plural = 'Parcels'
        ordering = ['-received_at']
        indexes = [
            models.Index(fields=['locker', 'status'], name='idx_parcel_locker_status'),
            models.Index(fields=['status', 'received_at'], name='idx_parcel_status_received'),
        ]
    
    def save(self, *args, **kwargs):
        if not self.display_id:
            self.display_id = generate_parcel_id(self.locker)
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.display_id}"
    
    @property
    def requires_action(self):
        return self.status == 'action_required'
    
    @property
    def is_ready_to_ship(self):
        return self.status == 'approved'

    @property
    def storage_days(self):
        from django.utils import timezone
        if not self.received_at:
            return 0
        delta = timezone.now() - self.received_at
        return delta.days

    @property
    def days_remaining_free(self):
        # 30 days free storage
        used = self.storage_days
        remaining = 30 - used
        return max(0, remaining)
    
    @property
    def is_storage_overdue(self):
        return self.days_remaining_free == 0

    @property
    def volumetric_weight_kg(self):
        """Calculate volumetric weight: (L x W x H) / 5000 (industry standard)."""
        if self.length_cm and self.width_cm and self.height_cm:
            vol = float(self.length_cm) * float(self.width_cm) * float(self.height_cm)
            return round(vol / 5000, 2)
        return None

    @property
    def billable_weight_kg(self):
        """Carriers charge by whichever is greater: actual or volumetric weight."""
        actual = float(self.weight_kg) if self.weight_kg else 0
        volumetric = self.volumetric_weight_kg or 0
        return max(actual, volumetric)


class ParcelImage(models.Model):
    """Inspection images for a parcel."""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    display_id = models.CharField(max_length=60, unique=True, editable=False, db_index=True, null=True, blank=True)
    
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name='images')
    image_path = models.CharField(max_length=500, blank=True)  # Supabase Storage file path
    is_primary = models.BooleanField(default=False)
    caption = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'Parcel Image'
        verbose_name_plural = 'Parcel Images'
        ordering = ['-is_primary', '-uploaded_at']
    
    def save(self, *args, **kwargs):
        if not self.display_id:
            self.display_id = generate_parcel_image_id(self.parcel)
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.display_id}"
    
    @property
    def image_url(self):
        """Get signed URL for the image (for backward compatibility)."""
        if self.image_path:
            try:
                from .utils import get_signed_parcel_image_url
                return get_signed_parcel_image_url(self.image_path)
            except Exception:
                return ''
        return ''


class ReturnRequest(models.Model):
    """Return request for a parcel."""
    
    STATUS_CHOICES = [
        ('requested', 'Requested'),
        ('approved', 'Approved'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('rejected', 'Rejected'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    display_id = models.CharField(max_length=60, unique=True, editable=False, db_index=True, null=True, blank=True)
    
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name='return_requests')
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='requested', db_index=True)
    
    # Return tracking
    return_tracking_number = models.CharField(max_length=100, blank=True)
    return_carrier = models.CharField(max_length=100, blank=True)
    
    # Timestamps
    requested_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    admin_notes = models.TextField(blank=True)
    
    class Meta:
        verbose_name = 'Return Request'
        verbose_name_plural = 'Return Requests'
        ordering = ['-requested_at']
    
    def save(self, *args, **kwargs):
        if not self.display_id:
            self.display_id = generate_return_request_id(self.parcel)
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.display_id}"


class DiscardRequest(models.Model):
    """Discard request for a parcel."""
    
    STATUS_CHOICES = [
        ('requested', 'Requested'),
        ('confirmed', 'Confirmed'),
        ('discarded', 'Discarded'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    display_id = models.CharField(max_length=60, unique=True, editable=False, db_index=True, null=True, blank=True)
    
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name='discard_requests')
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='requested', db_index=True)
    
    requested_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    discarded_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        verbose_name = 'Discard Request'
        verbose_name_plural = 'Discard Requests'
    
    def save(self, *args, **kwargs):
        if not self.display_id:
            self.display_id = generate_discard_request_id(self.parcel)
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.display_id}"
