import uuid
from django.db import models
from apps.accounts.models import User
from apps.locker.models import Parcel


def generate_shipment_id(user):
    """Generate sequential shipment ID: RB-12345-S001"""
    from apps.shipments.models import Shipment
    try:
        locker = user.locker
        locker_id = locker.locker_id
    except:
        locker_id = f"U{str(user.id)[:6].upper()}"
    
    count = Shipment.objects.filter(user=user).count() + 1
    return f"{locker_id}-S{count:03d}"


def generate_shipment_doc_id(shipment):
    """Generate sequential shipment document ID: RB-12345-S001-D001"""
    from apps.shipments.models import ShipmentDocument
    shipment_id = shipment.display_id
    count = ShipmentDocument.objects.filter(shipment=shipment).count() + 1
    return f"{shipment_id}-D{count:03d}"


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
        ('bluedart', 'BlueDart'),
        ('dtdc', 'DTDC'),
        ('delhivery', 'Delhivery'),
        ('other', 'Other'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    display_id = models.CharField(max_length=50, unique=True, editable=False, db_index=True, null=True, blank=True)
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='shipments')
    
    # Shipment type
    shipment_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    
    # Status
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='draft')
    
    # Carrier & tracking
    carrier = models.CharField(max_length=50, choices=CARRIER_CHOICES, blank=True)
    tracking_number = models.CharField(max_length=100, blank=True)
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
    
    # Pricing
    shipping_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default='INR')
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    
    # Notes
    admin_notes = models.TextField(blank=True)
    
    class Meta:
        verbose_name = 'Shipment'
        verbose_name_plural = 'Shipments'
        ordering = ['-created_at']
    
    def save(self, *args, **kwargs):
        if not self.display_id:
            self.display_id = generate_shipment_id(self.user)
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.display_id}"
    
    @property
    def item_count(self):
        return self.items.count()
    
    @property
    def is_active(self):
        return self.status in ['packing', 'dispatched', 'in_transit', 'customs', 'out_for_delivery']


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
    
    def __str__(self):
        return f"{self.shipment.display_id} - {self.status}"
