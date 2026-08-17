import uuid
from django.db import models, transaction
from apps.accounts.models import User
from apps.locker.models import Parcel
from apps.content.models import ShippingRate


def generate_shipment_id(user):
    """Generate sequential shipment ID: RB-12345-S001 (race-condition safe)."""
    from apps.shipments.models import Shipment
    try:
        locker = user.locker
        locker_id = locker.locker_id
    except:
        locker_id = f"U{str(user.id)[:6].upper()}"
    
    with transaction.atomic():
        last = (
            Shipment.objects
            .select_for_update()
            .filter(user=user)
            .order_by('-created_at')
            .first()
        )
        if last and last.display_id:
            try:
                num = int(last.display_id.rsplit('-S', 1)[1]) + 1
            except (ValueError, IndexError):
                num = Shipment.objects.filter(user=user).count() + 1
        else:
            num = 1
    return f"{locker_id}-S{num:03d}"


def generate_shipment_doc_id(shipment):
    """Generate sequential shipment document ID: RB-12345-S001-D001 (race-condition safe)."""
    from apps.shipments.models import ShipmentDocument
    shipment_id = shipment.display_id
    with transaction.atomic():
        last = (
            ShipmentDocument.objects
            .select_for_update()
            .filter(shipment=shipment)
            .order_by('-uploaded_at')
            .first()
        )
        if last and last.display_id:
            try:
                num = int(last.display_id.rsplit('-D', 1)[1]) + 1
            except (ValueError, IndexError):
                num = ShipmentDocument.objects.filter(shipment=shipment).count() + 1
        else:
            num = 1
    return f"{shipment_id}-D{num:03d}"


class Shipment(models.Model):
    """Shipment containing multiple parcels."""
    
    TYPE_CHOICES = [
        ('international', 'International'),
        ('domestic', 'Domestic (India)'),
    ]
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('declaration_pending', 'Declaration Approval Pending'),
        ('pending_payment', 'Pending Payment'),
        ('packing', 'Packing'),
        ('dispatched', 'Dispatched'),
        ('in_transit', 'In Transit'),
        ('customs', 'At Customs'),
        ('out_for_delivery', 'Out for Delivery'),
        ('delivered', 'Delivered'),
        ('returned', 'Returned to Sender'),
        ('cancelled', 'Cancelled'),
    ]
    
    CARRIER_CHOICES = [
        ('dhl', 'DHL Express'),
        ('fedex', 'FedEx'),
        ('ups', 'UPS'),
        ('aramex', 'Aramex'),
        ('bluedart', 'BlueDart'),
        ('dtdc', 'DTDC'),
        ('delhivery', 'Delhivery'),
        ('other', 'Other'),
    ]
    
    PAYMENT_STATUS_CHOICES = [
        ('unpaid', 'Unpaid'),
        ('partial', 'Partially Paid'),
        ('paid', 'Paid'),
        ('refunded', 'Refunded'),
    ]

    SERVICE_TYPE_CHOICES = ShippingRate.SERVICE_TYPE_CHOICES

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    display_id = models.CharField(max_length=50, unique=True, editable=False, db_index=True, null=True, blank=True)
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='shipments')
    
    # Shipment type
    shipment_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    
    # Status
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='draft', db_index=True)
    
    # Payment
    payment_status = models.CharField(
        max_length=20, choices=PAYMENT_STATUS_CHOICES, default='unpaid', db_index=True,
        help_text="Payment status for this shipment"
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    
    # Carrier & tracking
    carrier = models.CharField(max_length=50, choices=CARRIER_CHOICES, blank=True)
    tracking_number = models.CharField(max_length=100, blank=True, db_index=True)
    tracking_url = models.URLField(blank=True)
    
    # Destination
    recipient_name = models.CharField(max_length=255)
    recipient_phone = models.CharField(max_length=20)
    recipient_email = models.EmailField(blank=True)
    address_line1 = models.CharField(max_length=255)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20)
    country = models.CharField(max_length=100, default='India')
    
    # Weight & dimensions
    total_weight_kg = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    # Shipping speed selected by the customer once total_weight_kg is known
    service_type = models.CharField(max_length=20, choices=SERVICE_TYPE_CHOICES, blank=True)

    # Pricing
    shipping_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    consolidation_fee = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Locked in at shipment creation, regardless of parcel count."
    )
    currency = models.CharField(max_length=3, default='INR')
    
    # Estimated delivery
    estimated_delivery_date = models.DateField(
        null=True, blank=True,
        help_text="Estimated delivery date shown to customer"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    
    # Cancellation tracking
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.CharField(max_length=255, blank=True)
    
    # Notes
    admin_notes = models.TextField(blank=True)
    
    class Meta:
        verbose_name = 'Shipment'
        verbose_name_plural = 'Shipments'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status'], name='idx_shipment_user_status'),
            models.Index(fields=['status', 'created_at'], name='idx_shipment_status_date'),
            models.Index(fields=['carrier', 'tracking_number'], name='idx_shipment_carrier_track'),
        ]
    
    def save(self, *args, **kwargs):
        if not self.display_id:
            self.display_id = generate_shipment_id(self.user)
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.display_id}"

    def approve_declaration(self):
        """Move a declaration_pending shipment forward once staff approve the
        customs declaration. Goes to pending_payment unless already paid (then
        packing directly). No-op (returns False) if the shipment isn't in
        declaration_pending. Shipping cost is no longer set by staff here —
        the customer sets it by choosing a shipping speed on the shipment
        detail page once the weight is known."""
        if self.status != 'declaration_pending':
            return False
        self.status = 'packing' if self.payment_status == 'paid' else 'pending_payment'
        self.save(update_fields=['status'])
        return True

    def advance_after_payment(self):
        """Called once payment_status is set to 'paid' — moves a shipment
        waiting on payment into packing. No-op for any other status.

        Note: this only mutates self.status in memory — it does NOT save.
        Callers are expected to set payment_status/paid_at and call this in
        the same transaction, then save once (see apps/payments/views.py),
        so the transition batches into the same write as the payment update."""
        if self.status == 'pending_payment':
            self.status = 'packing'
            return True
        return False

    @property
    def item_count(self):
        return self.items.count()
    
    @property
    def is_active(self):
        return self.status in ['packing', 'dispatched', 'in_transit', 'customs', 'out_for_delivery']

    @property
    def stage(self):
        """4-step tracker (Picked Up / Dispatched / Customs / Delivered) for the
        shipments list UI. Returns None for returned/cancelled shipments, which fall
        outside the linear happy path and render their own closed-state badge."""
        if self.status in ('returned', 'cancelled'):
            return None

        dispatched_done = self.status in ('dispatched', 'in_transit', 'customs', 'out_for_delivery', 'delivered')
        customs_done = self.status in ('customs', 'out_for_delivery', 'delivered')
        delivered_done = self.status == 'delivered'

        return [
            {'key': 'picked_up', 'label': 'Picked Up', 'complete': True, 'date': self.created_at},
            {'key': 'dispatched', 'label': 'Dispatched', 'complete': dispatched_done, 'date': self.dispatched_at},
            {'key': 'customs', 'label': 'Customs', 'complete': customs_done, 'date': None},
            {'key': 'delivered', 'label': 'Delivered', 'complete': delivered_done, 'date': self.delivered_at},
        ]

    @property
    def badge_class(self):
        """Status-badge CSS variant for this shipment's current status —
        single source of truth so templates don't each re-derive it."""
        return {
            'delivered': 'status-approved',
            'returned': 'status-returned',
            'cancelled': 'status-action',
        }.get(self.status, 'status-pending')

    @property
    def status_message(self):
        """One-line human status summary for the shipment detail page header."""
        return {
            'draft': 'Your shipment is being prepared.',
            'declaration_pending': 'Your customs declaration is under review.',
            'pending_payment': 'Awaiting payment to begin processing.',
            'packing': 'Your items are being packed at our warehouse.',
            'dispatched': 'Your shipment has been dispatched.',
            'in_transit': 'Your shipment is on the way.',
            'customs': 'Your shipment is at customs, awaiting clearance.',
            'out_for_delivery': 'Your shipment is out for delivery.',
            'delivered': 'Your shipment has been delivered.',
            'returned': 'Your shipment was returned to sender.',
            'cancelled': 'This shipment was cancelled.',
        }.get(self.status, '')


class ShipmentItem(models.Model):
    """Link between Shipment and Parcel."""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name='items')
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name='shipment_items')
    added_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'Shipment Item'
        verbose_name_plural = 'Shipment Items'
        unique_together = ['shipment', 'parcel']
    
    def __str__(self):
        return f"{self.parcel.display_id} in {self.shipment.display_id}"


class ShipmentDocument(models.Model):
    """Documents related to a shipment (customs forms, invoices, etc)."""
    
    DOCUMENT_TYPES = [
        ('invoice', 'Commercial Invoice'),
        ('customs', 'Customer Declaration'),
        ('packing', 'Packing List'),
        ('label', 'Shipping Label'),
        ('other', 'Other'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    display_id = models.CharField(max_length=60, unique=True, editable=False, db_index=True, null=True, blank=True)
    
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name='documents')
    document_type = models.CharField(max_length=20, choices=DOCUMENT_TYPES)
    document_url = models.CharField(max_length=500)  # Supabase Storage file path
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'Shipment Document'
        verbose_name_plural = 'Shipment Documents'
    
    def save(self, *args, **kwargs):
        if not self.display_id:
            self.display_id = generate_shipment_doc_id(self.shipment)
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.display_id} ({self.get_document_type_display()})"
    
    @property
    def signed_url(self):
        """Get signed URL for private document access."""
        if self.document_url:
            from apps.locker.utils import get_signed_shipment_doc_url
            try:
                return get_signed_shipment_doc_url(self.document_url)
            except:
                return ''
        return ''


class TrackingEvent(models.Model):
    """Tracking event history for a shipment."""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name='tracking_events')
    
    status = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=200, blank=True)
    
    # Timestamp from carrier
    event_timestamp = models.DateTimeField(null=True, blank=True)
    
    # When we recorded this event
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Raw data from carrier API
    raw_data = models.JSONField(default=dict, blank=True)
    
    class Meta:
        verbose_name = 'Tracking Event'
        verbose_name_plural = 'Tracking Events'
        ordering = ['-event_timestamp', '-created_at']
        indexes = [
            models.Index(fields=['shipment', 'event_timestamp'], name='idx_tracking_shipment_time'),
        ]
    
    def __str__(self):
        return f"{self.shipment.display_id} - {self.status}"
