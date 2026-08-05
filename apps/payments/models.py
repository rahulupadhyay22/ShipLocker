import uuid
from django.db import models, transaction
from apps.accounts.models import User
from apps.shipments.models import Shipment
from apps.locker.models import Parcel


def generate_payment_id(user):
    """Generate sequential payment ID: RB-12345-PAY001 (race-condition safe)."""
    try:
        locker = user.locker
        locker_id = locker.locker_id
    except Exception:
        locker_id = f"U{str(user.id)[:6].upper()}"

    with transaction.atomic():
        last = (
            Payment.objects
            .select_for_update()
            .filter(user=user)
            .order_by('-created_at')
            .first()
        )
        if last and last.display_id:
            try:
                num = int(last.display_id.rsplit('-PAY', 1)[1]) + 1
            except (ValueError, IndexError):
                num = Payment.objects.filter(user=user).count() + 1
        else:
            num = 1
    return f"{locker_id}-PAY{num:03d}"


class Payment(models.Model):
    """Payment transaction record (Razorpay, bank transfer, manual, etc.)."""

    PAYMENT_METHOD_CHOICES = [
        ('razorpay', 'Razorpay'),
        ('bank_transfer', 'Bank Transfer'),
        ('upi', 'UPI'),
        ('cash', 'Cash'),
        ('manual', 'Manual / Offline'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('authorized', 'Authorized'),
        ('captured', 'Captured / Paid'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
        ('partially_refunded', 'Partially Refunded'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    display_id = models.CharField(
        max_length=60, unique=True, editable=False, db_index=True,
        null=True, blank=True
    )

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='payments'
    )
    shipment = models.ForeignKey(
        Shipment, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='payments',
        help_text="Shipment this payment is for (if applicable)"
    )

    # Amount
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='INR')

    # Method & Status
    payment_method = models.CharField(
        max_length=20, choices=PAYMENT_METHOD_CHOICES, default='razorpay'
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='pending'
    )

    # Razorpay-specific fields
    razorpay_order_id = models.CharField(
        max_length=100, blank=True, db_index=True,
        help_text="Razorpay Order ID (order_xxxxx)"
    )
    razorpay_payment_id = models.CharField(
        max_length=100, blank=True, db_index=True,
        help_text="Razorpay Payment ID (pay_xxxxx)"
    )
    razorpay_signature = models.CharField(
        max_length=500, blank=True,
        help_text="Razorpay payment signature for verification"
    )

    # Refund tracking
    refund_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Amount refunded (if any)"
    )
    refund_id = models.CharField(
        max_length=100, blank=True,
        help_text="Razorpay Refund ID (rfnd_xxxxx)"
    )

    # Metadata
    description = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Payment'
        verbose_name_plural = 'Payments'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status'], name='idx_payment_user_status'),
            models.Index(fields=['status', 'created_at'], name='idx_payment_status_date'),
        ]

    def save(self, *args, **kwargs):
        if not self.display_id:
            self.display_id = generate_payment_id(self.user)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.display_id} — ₹{self.amount} ({self.get_status_display()})"

    @property
    def is_successful(self):
        return self.status == 'captured'

    @property
    def is_refundable(self):
        return self.status == 'captured' and not self.refund_id


class StorageFee(models.Model):
    """Storage fee charged when parcel exceeds free storage period."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('waived', 'Waived'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    parcel = models.ForeignKey(
        Parcel, on_delete=models.CASCADE, related_name='storage_fees'
    )
    payment = models.ForeignKey(
        Payment, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='storage_fees',
        help_text="Payment that covered this fee"
    )

    # Fee details
    fee_amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='INR')
    days_overdue = models.PositiveIntegerField(
        help_text="Number of days beyond free storage period"
    )

    # Status
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default='pending'
    )
    waived_reason = models.CharField(max_length=255, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Storage Fee'
        verbose_name_plural = 'Storage Fees'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.parcel.display_id} — ₹{self.fee_amount} ({self.get_status_display()})"


class Invoice(models.Model):
    """GST invoice generated once a shipment's payment is marked paid.

    Every field that could change later on the Shipment/User/Payment is
    snapshotted here at generation time, so this record stays a correct
    historical document no matter what happens to the shipment afterward.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shipment = models.OneToOneField(
        Shipment, on_delete=models.PROTECT, related_name='invoice'
    )
    invoice_number = models.CharField(max_length=30, unique=True, db_index=True)
    invoice_date = models.DateTimeField(
        help_text="The payment's paid_at timestamp, not whenever the PDF happened to be generated"
    )

    # Snapshotted customer details
    customer_name = models.CharField(max_length=255)
    customer_email = models.EmailField(blank=True)
    billing_address = models.TextField()
    customer_gstin = models.CharField(max_length=20, blank=True)

    # Snapshotted payment reference
    payment_reference = models.CharField(max_length=100, blank=True)
    payment_method = models.CharField(max_length=50, blank=True)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2)

    # Snapshotted charges
    shipping_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    storage_fee_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    consolidation_fee_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    taxable_amount = models.DecimalField(max_digits=10, decimal_places=2)

    # GST breakdown
    is_zero_rated = models.BooleanField(default=False)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    cgst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    sgst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    igst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)

    pdf_document_url = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Invoice'
        verbose_name_plural = 'Invoices'
        ordering = ['-invoice_date']

    def __str__(self):
        return self.invoice_number
