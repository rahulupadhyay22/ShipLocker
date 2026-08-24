import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import Locker, User


def generate_personal_shop_request_id(locker):
    """Generate sequential TrunkAssist request ID: CT-HYD-483921-TA001.

    Locks the parent Locker row (not just existing children) so the very
    first request for a brand-new locker is also race-safe under
    concurrent submits.
    """
    from apps.personal_shop.models import PersonalShopRequest

    with transaction.atomic():
        locked_locker = Locker.objects.select_for_update().get(pk=locker.pk)
        last = (
            PersonalShopRequest.objects
            .select_for_update()
            .filter(locker=locked_locker)
            .order_by('-created_at')
            .first()
        )
        if last and last.display_id:
            try:
                num = int(last.display_id.rsplit('-TA', 1)[1]) + 1
            except (ValueError, IndexError):
                num = PersonalShopRequest.objects.filter(locker=locked_locker).count() + 1
        else:
            num = 1
    return f"{locked_locker.locker_id}-TA{num:03d}"


class PersonalShopRequest(models.Model):
    REQUEST_TYPE_CHOICES = [
        ('product_link', 'Product Link'),
        ('image_search', 'Image Search'),
        ('cart_screenshot', 'Cart Screenshot'),
        ('boutique_purchase', 'Boutique Purchase'),
        ('local_shop_purchase', 'Local Shop Purchase'),
        ('custom_request', 'Custom Request'),
    ]

    STATUS_CHOICES = [
        ('submitted', 'Submitted'),
        ('reviewing', 'Reviewing'),
        ('executive_assigned', 'Executive Assigned'),
        ('searching', 'Searching'),
        ('quotation_ready', 'Quotation Ready'),
        ('quotation_declined', 'Quotation Declined'),
        ('quotation_expired', 'Quotation Expired'),
        ('payment_pending', 'Payment Pending'),
        ('paid', 'Paid'),
        ('purchased', 'Purchased'),
        ('delivered_to_warehouse', 'Delivered to Warehouse'),
        ('added_to_trunk', 'Added to Trunk'),
        ('cancelled', 'Cancelled'),
        ('needs_info', 'Needs Info'),
    ]

    EDITABLE_STATUSES = {'submitted', 'reviewing', 'executive_assigned', 'searching', 'needs_info'}
    NON_CANCELLABLE_STATUSES = {'purchased', 'delivered_to_warehouse', 'added_to_trunk', 'cancelled'}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    display_id = models.CharField(max_length=60, unique=True, editable=False, db_index=True, null=True, blank=True)
    locker = models.ForeignKey(Locker, on_delete=models.CASCADE, related_name='personal_shop_requests')
    request_type = models.CharField(max_length=30, choices=REQUEST_TYPE_CHOICES, db_index=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='submitted', db_index=True)
    assigned_executive = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assigned_personal_shop_requests',
        limit_choices_to={'is_staff': True},
    )
    product_url = models.URLField(max_length=1000, null=True, blank=True, db_index=True)
    shop_name = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    boutique_name = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    type_details = models.JSONField(default=dict, blank=True)
    parcel = models.ForeignKey(
        'locker.Parcel', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='personal_shop_request',
    )
    source_parcel = models.ForeignKey(
        'locker.Parcel', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='referencing_personal_shop_requests',
        help_text="Existing trunk parcel the user picked as the basis for a Boutique Purchase "
                   "(e.g. 'buy from my trunk'), distinct from `parcel` (the new parcel created "
                   "once this request is purchased and delivered).",
    )
    active_quotation = models.ForeignKey(
        'PersonalShopQuotation', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    refund_required = models.BooleanField(default=False)

    executive_assigned_at = models.DateTimeField(null=True, blank=True)
    searching_started_at = models.DateTimeField(null=True, blank=True)
    quotation_ready_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    purchased_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    added_to_trunk_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    work_started_at = models.DateTimeField(null=True, blank=True)
    work_started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
        help_text="Staff member who marked work started — audit trail for a non-refundable fee decision.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'TrunkAssist Request'
        verbose_name_plural = 'TrunkAssist Requests'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['locker', 'status'], name='idx_pshop_locker_status'),
        ]
        permissions = [
            ('mark_work_started', 'Can mark TrunkAssist work as started (locks fee as non-refundable)'),
        ]

    # Auto-stamp the matching timeline timestamp whenever status is set to it directly
    # (e.g. via the admin's status dropdown) instead of through a dedicated action/view.
    STATUS_TIMESTAMP_FIELDS = {
        'executive_assigned': 'executive_assigned_at',
        'searching': 'searching_started_at',
        'quotation_ready': 'quotation_ready_at',
        'paid': 'paid_at',
        'purchased': 'purchased_at',
        'delivered_to_warehouse': 'delivered_at',
        'added_to_trunk': 'added_to_trunk_at',
        'cancelled': 'cancelled_at',
    }

    def save(self, *args, **kwargs):
        if not self.display_id:
            self.display_id = generate_personal_shop_request_id(self.locker)
        timestamp_field = self.STATUS_TIMESTAMP_FIELDS.get(self.status)
        if timestamp_field and getattr(self, timestamp_field) is None:
            setattr(self, timestamp_field, timezone.now())
        if self.status == 'added_to_trunk' and self.delivered_at is None:
            self.delivered_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.display_id}"

    @property
    def is_editable(self):
        return self.status in self.EDITABLE_STATUSES and self.active_quotation_id is None

    @property
    def is_cancellable(self):
        return self.status not in self.NON_CANCELLABLE_STATUSES

    def mark_paid(self):
        """Advance to paid + approve the active quotation after a captured payment.

        No-ops if the request was cancelled in the meantime (e.g. the user cancelled
        while a Razorpay checkout was still open) so a late-arriving payment can't
        resurrect a cancelled request.
        """
        if self.status == 'cancelled':
            return False
        self.status = 'paid'
        self.save()
        if self.active_quotation and self.active_quotation.status == 'pending':
            self.active_quotation.status = 'approved'
            self.active_quotation.save()
        return True

    # request_type -> {'primary': (real_model_field, label) or None, 'details': [(type_details key, label), ...]}
    # Drives both `product_summary` (list-page one-liner) and `detail_pairs` (detail-page card).
    TYPE_DETAIL_FIELDS = {
        'product_link': {
            'primary': ('product_url', 'Product URL'),
            'details': [('quantity', 'Quantity'), ('size', 'Size'), ('colour', 'Colour'), ('notes', 'Notes')],
        },
        'image_search': {'primary': None, 'details': [('description', 'Description')]},
        'cart_screenshot': {'primary': None, 'details': [('description', 'Description')]},
        'boutique_purchase': {
            'primary': ('boutique_name', 'Boutique'),
            'details': [('item_description', 'Item'), ('preferred_size', 'Size')],
        },
        'local_shop_purchase': {
            'primary': ('shop_name', 'Shop'),
            'details': [
                ('city', 'City'), ('item_description', 'Item'),
                ('shop_address', 'Shop Address'), ('maps_link', 'Maps Link'), ('shop_phone', 'Shop Contact'),
            ],
        },
        'custom_request': {'primary': None, 'details': [('description', 'Description')]},
    }

    @property
    def product_summary(self):
        config = self.TYPE_DETAIL_FIELDS.get(self.request_type, {})
        primary = config.get('primary')
        if primary:
            field_name, _ = primary
            value = getattr(self, field_name)
            if not value and self.source_parcel_id:
                return f"From My Trunk – {self.source_parcel.display_id}"
            return value or '–'
        d = self.type_details or {}
        return d.get('item_description') or d.get('description') or '–'

    @property
    def detail_pairs(self):
        """Ordered (label, value) pairs for the Product Details card, tailored to request_type."""
        config = self.TYPE_DETAIL_FIELDS.get(self.request_type, {})
        d = self.type_details or {}
        pairs = []
        primary = config.get('primary')
        if primary:
            field_name, label = primary
            value = getattr(self, field_name)
            if not value and self.source_parcel_id:
                value = f"From My Trunk – {self.source_parcel.display_id} ({self.source_parcel.item_name or 'item'})"
            pairs.append((label, value or '–'))
        for key, label in config.get('details', []):
            value = d.get(key)
            if not value:
                continue
            if key == 'city':
                value = value.title()
            pairs.append((label, value))
        return pairs


class PersonalShopImage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(PersonalShopRequest, on_delete=models.CASCADE, related_name='images')
    image_path = models.CharField(max_length=500, blank=True)
    caption = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'TrunkAssist Image'
        verbose_name_plural = 'TrunkAssist Images'
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"Image for {self.request.display_id}"

    @property
    def image_url(self):
        if not self.image_path:
            return ''
        from apps.locker.utils import get_signed_parcel_image_url
        return get_signed_parcel_image_url(self.image_path)


class PersonalShopNote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(PersonalShopRequest, on_delete=models.CASCADE, related_name='notes')
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='personal_shop_notes')
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'TrunkAssist Note'
        verbose_name_plural = 'TrunkAssist Notes'
        ordering = ['-created_at']

    def __str__(self):
        return f"Note on {self.request.display_id}"


# Which quotation_type values are valid for which request_type — per spec 10's
# §8/§10 rules: research_fee only for Custom Request; expense_advance only for
# the physical-visit request types (Boutique Purchase, Local Shop Purchase).
# 'purchase' has no entry here — it's valid for every request_type.
QUOTATION_TYPE_ALLOWED_REQUEST_TYPES = {
    'research_fee': {'custom_request'},
    'expense_advance': {'boutique_purchase', 'local_shop_purchase'},
}


class PersonalShopQuotation(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('declined', 'Declined'),
        ('expired', 'Expired'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(PersonalShopRequest, on_delete=models.CASCADE, related_name='quotations')
    quotation_type = models.CharField(
        max_length=20,
        choices=[
            ('purchase', 'Purchase'),
            ('research_fee', 'Research Fee'),
            ('expense_advance', 'Expense Advance'),
        ],
        default='purchase',
    )
    domestic_shipping_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    service_fee_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    service_fee_standard_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    service_fee_manual_override = models.BooleanField(default=False)
    research_fee_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        validators=[MinValueValidator(Decimal('0'))],
        help_text="Only used on Research Fee quotations — auto-suggested when Quotation type is set to Research Fee.",
    )
    travel_expense_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        validators=[MinValueValidator(Decimal('0'))],
    )
    payment_gateway_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    valid_until = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'TrunkAssist Quotation'
        verbose_name_plural = 'TrunkAssist Quotations'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['request'],
                condition=models.Q(status='pending'),
                name='uniq_pending_quotation_per_request',
            ),
        ]
        permissions = [
            ('override_service_fee', 'Can override the auto-suggested TrunkAssist service fee'),
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._loaded_quotation_type = self.quotation_type
        self._loaded_status = self.status

    def clean(self):
        super().clean()
        self._check_quotation_type_matches_request_type(as_field_error=True)

    def save(self, *args, **kwargs):
        if self.pk and self._loaded_status == 'approved' and self._loaded_quotation_type != self.quotation_type:
            raise ValidationError("Cannot change quotation_type after the quotation has been approved.")
        self._check_quotation_type_matches_request_type(as_field_error=False)
        super().save(*args, **kwargs)
        self._loaded_quotation_type = self.quotation_type
        self._loaded_status = self.status

    def _check_quotation_type_matches_request_type(self, as_field_error):
        """research_fee is only valid on a Custom Request; expense_advance only
        on Boutique/Local Shop Purchase — see QUOTATION_TYPE_ALLOWED_REQUEST_TYPES.
        Called from both clean() (admin-form UX, a field-specific error) and
        save() (the actual hard guarantee, holds for shell/script edits too)."""
        if not self.request_id:
            return
        allowed = QUOTATION_TYPE_ALLOWED_REQUEST_TYPES.get(self.quotation_type)
        if allowed is None or self.request.request_type in allowed:
            return
        message = (
            f"quotation_type '{self.get_quotation_type_display()}' is not valid for a "
            f"'{self.request.get_request_type_display()}' request."
        )
        if as_field_error:
            raise ValidationError({'quotation_type': message})
        raise ValidationError(message)

    def __str__(self):
        return f"Quotation for {self.request.display_id} (₹{self.total_amount})"

    @property
    def is_expired(self):
        return self.status == 'pending' and timezone.now() > self.valid_until

    @property
    def premium_discount_amount(self):
        return max(Decimal('0.00'), self.service_fee_standard_amount - self.service_fee_amount)

    def refresh_service_fee_discount(self):
        """Recompute service_fee_amount/total_amount from service_fee_standard_amount
        against the request's *current* locker plan. No-op once status != 'pending'
        (locked historical charge) or quotation_type != 'purchase' (fee unused).
        request.locker is a required, on_delete=CASCADE FK (never null/dangling —
        unlike Shipment.user.locker, which is a lazily-created reverse OneToOne),
        so no getattr guard is structurally necessary here; one is added anyway
        for visual parity with the identical Phase C pattern."""
        if self.status != 'pending' or self.quotation_type != 'purchase':
            return
        locker = getattr(self.request, 'locker', None)
        if locker is None:
            return
        new_fee, _discount = locker.apply_service_fee_discount(self.service_fee_standard_amount)
        if new_fee == self.service_fee_amount:
            return
        self.service_fee_amount = new_fee
        subtotal = sum((item.line_total for item in self.line_items.all()), start=Decimal('0'))
        self.total_amount = subtotal + self.domestic_shipping_amount + self.service_fee_amount + self.payment_gateway_charge
        self.save(update_fields=['service_fee_amount', 'total_amount'])

    @property
    def is_refundable(self):
        """Post-payment refund-eligibility check only — always True before payment,
        since work_started_at can't be set until the quotation is approved. Must not
        drive any pre-payment UI; the quotation template's warning is quotation_type-driven."""
        if self.quotation_type in ('research_fee', 'expense_advance') and self.request.work_started_at is not None:
            return False
        return True


class PersonalShopQuotationLineItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quotation = models.ForeignKey(PersonalShopQuotation, on_delete=models.CASCADE, related_name='line_items')
    name = models.CharField(max_length=255)
    thumbnail_url = models.URLField(max_length=1000, blank=True)
    variant_details = models.CharField(max_length=255, blank=True)
    qty = models.PositiveIntegerField(default=1)
    unit_amount = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = 'Quotation Line Item'
        verbose_name_plural = 'Quotation Line Items'

    def __str__(self):
        return f"{self.name} x{self.qty}"

    @property
    def line_total(self):
        return self.qty * self.unit_amount
