import secrets
import uuid
from decimal import Decimal, ROUND_HALF_UP
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.conf import settings


class UserManager(BaseUserManager):
    """Custom user manager for email-based authentication."""
    
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        user.save(using=self._db)
        return user
    
    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """Custom User model with Supabase Auth integration."""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    supabase_id = models.CharField(max_length=255, unique=True, null=True, blank=True, db_index=True)
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=20, blank=True, db_index=True)
    whatsapp_number = models.CharField(max_length=20, blank=True)
    whatsapp_verified = models.BooleanField(default=False)
    email_verified = models.BooleanField(default=False, help_text="Whether user's email has been verified")
    
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)
    last_login = models.DateTimeField(null=True, blank=True)
    
    objects = UserManager()
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []
    
    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        ordering = ['-date_joined']
    
    def __str__(self):
        return self.email
    
    def get_full_name(self):
        return self.full_name or self.email.split('@')[0]
    
    def get_short_name(self):
        return self.full_name.split()[0] if self.full_name else self.email.split('@')[0]


TRUNK_ID_PREFIX = "CT-HYD"


def generate_locker_id():
    """Generate a unique Trunk ID like CT-HYD-483921 with collision retry."""
    from apps.accounts.models import Locker
    for _ in range(10):
        number = f"{secrets.randbelow(1_000_000):06d}"
        new_id = f"{TRUNK_ID_PREFIX}-{number}"
        if not Locker.objects.filter(locker_id=new_id).exists():
            return new_id
    raise ValueError("Unable to generate unique locker ID after 10 attempts")


class Locker(models.Model):
    """Virtual locker assigned to each user."""

    PLAN_CHOICES = [('free', 'Free'), ('paid', 'Paid')]
    PREMIUM_SERVICE_FEE_DISCOUNT_RATE = Decimal('0.25')
    PREMIUM_SHIPPING_DISCOUNT_RATE = Decimal('0.05')
    PREMIUM_STORAGE_DISCOUNT_RATE = Decimal('0.20')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='locker')
    locker_id = models.CharField(max_length=20, unique=True, default=generate_locker_id)
    is_active = models.BooleanField(default=True, help_text="Whether this locker is active")
    plan_type = models.CharField(max_length=10, choices=PLAN_CHOICES, default='free')
    payment_grace_until = models.DateTimeField(
        null=True, blank=True,
        help_text="Set when a paid-plan renewal fails; non-null and not yet expired means the account is in its 7-day grace period."
    )
    premium_expires_at = models.DateField(
        null=True, blank=True,
        help_text="Date the current Premium subscription term ends. Null for Free-plan lockers."
    )
    premium_savings_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        help_text=(
            "Denormalized running total (spec 11a) — real Premium discount already "
            "applied if this locker is Premium, or the hypothetical if it's Free. "
            "Incremented at the exact moment a quotation/shipment/batch charge is "
            "finalized as paid (see record_premium_savings()); never recomputed "
            "live via aggregate queries on page load."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Locker'
        verbose_name_plural = 'Lockers'
    
    def __str__(self):
        return f"{self.locker_id} - {self.user.get_full_name()}"
    
    @property
    def address(self):
        """Return the common locker address from AppSettings (admin-editable)."""
        try:
            from apps.notifications.models import AppSettings
            app_settings = AppSettings.get_settings()
            if app_settings.warehouse_address:
                return app_settings.warehouse_address
        except Exception:
            pass
        return settings.LOCKER_ADDRESS
    
    @property
    def email(self):
        """Return formatted locker email."""
        return f"{self.locker_id.lower()}@cameltrunk.com"
    
    @property
    def phone(self):
        """Return warehouse contact phone from AppSettings (admin-editable)."""
        try:
            from apps.notifications.models import AppSettings
            app_settings = AppSettings.get_settings()
            if app_settings.support_phone:
                return app_settings.support_phone
        except Exception:
            pass
        return "+91 9876543210"

    @property
    def is_premium(self):
        """Return True if locker has paid plan."""
        return self.plan_type == 'paid'

    def apply_service_fee_discount(self, standard_amount):
        """Apply 25% discount to service fee if premium; returns (discounted_amount, discount_amount)."""
        if not self.is_premium or standard_amount is None:
            return standard_amount, Decimal('0.00')
        discount = (standard_amount * self.PREMIUM_SERVICE_FEE_DISCOUNT_RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return (standard_amount - discount), discount

    def apply_shipping_discount(self, standard_amount):
        """Apply 5% discount to shipping if premium; returns (discounted_amount, discount_amount)."""
        if not self.is_premium or standard_amount is None:
            return standard_amount, Decimal('0.00')
        discount = (standard_amount * self.PREMIUM_SHIPPING_DISCOUNT_RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return (standard_amount - discount), discount

    def apply_storage_discount(self, standard_amount):
        """Apply 20% discount to daily storage charge if premium; returns (discounted_amount, discount_amount)."""
        if not self.is_premium or standard_amount is None:
            return standard_amount, Decimal('0.00')
        discount = (standard_amount * self.PREMIUM_STORAGE_DISCOUNT_RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return (standard_amount - discount), discount

    def premium_free_service(self):
        """Return True if locker has paid plan (eligible for free service features)."""
        return self.is_premium

    def record_premium_savings(self, standard_amount, rate):
        """Increment premium_savings_amount by standard_amount * rate — called
        at the exact moment a quotation/shipment/batch charge is finalized as
        paid (see apps/personal_shop/models.py::mark_paid,
        apps/payments/views.py's shipment/_mark_batch_charges_paid blocks).

        Always standard_amount * rate, regardless of whether this locker is
        currently Premium: when Premium, that discount was actually applied
        (standard - actual == standard * rate by construction of
        apply_*_discount), so this is real money saved; when Free, it's the
        hypothetical. Same formula either way — only the *label* shown at
        display time (premium_savings_display) depends on current plan_type.

        Uses an atomic F()-expression UPDATE, not read-modify-write on self,
        so concurrent payments for the same locker (e.g. a shipment payment
        and a storage-batch payment landing in the same second) can't clobber
        each other. Works even when self isn't a fully-loaded instance —
        only self.pk is used — so callers can pass a bare Locker(pk=locker_id).
        """
        if not standard_amount:
            return
        increment = (standard_amount * rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if increment <= 0:
            return
        Locker.objects.filter(pk=self.pk).update(
            premium_savings_amount=models.F('premium_savings_amount') + increment
        )

    @property
    def premium_savings_display(self):
        """Zero-query read of the denormalized premium_savings_amount, shaped
        for templates/accounts/_premium_savings_banner.html — same dict shape
        apps.accounts.services.calculate_premium_savings() used to compute
        live via aggregate queries on every page load (spec 11a)."""
        amount = self.premium_savings_amount or Decimal('0.00')
        if amount <= 0:
            return {'is_premium': self.is_premium, 'amount': Decimal('0.00'), 'label': ''}
        if self.is_premium:
            label = f"You've saved ₹{amount} with Premium so far"
        else:
            label = f"You could have saved ₹{amount} with Premium so far — upgrade now"
        return {'is_premium': self.is_premium, 'amount': amount, 'label': label}


class KYCDocument(models.Model):
    """KYC documents uploaded by users."""
    
    DOCUMENT_TYPES = [
        ('aadhaar', 'Aadhaar Card'),
        ('passport', 'Passport'),
        ('other', 'Other'),
    ]
    
    STATUS_CHOICES = [
        ('pending', 'Pending Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='kyc_documents')
    document_type = models.CharField(max_length=20, choices=DOCUMENT_TYPES)
    document_url = models.CharField(max_length=500)  # Supabase Storage file path
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reviewed_kyc_documents',
        help_text="Admin who reviewed this document"
    )
    notes = models.TextField(blank=True)
    
    class Meta:
        verbose_name = 'KYC Document'
        verbose_name_plural = 'KYC Documents'
        ordering = ['-uploaded_at']
    
    def __str__(self):
        return f"{self.user.email} - {self.get_document_type_display()}"
    
    @property
    def is_approved(self):
        return self.status == 'approved'


class SavedAddress(models.Model):
    """Saved shipping addresses for quick reuse across shipments."""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='saved_addresses')
    
    label = models.CharField(
        max_length=50, blank=True,
        help_text="A friendly name like 'Home', 'Office', 'Mom's Place'"
    )
    is_default = models.BooleanField(default=False, help_text="Use as default shipping address")
    
    # Recipient
    recipient_name = models.CharField(max_length=255)
    recipient_phone = models.CharField(max_length=20)
    recipient_email = models.EmailField(blank=True)
    
    # Address
    address_line1 = models.CharField(max_length=255)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20)
    country = models.CharField(max_length=100, default='India')
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Saved Address'
        verbose_name_plural = 'Saved Addresses'
        ordering = ['-is_default', '-updated_at']
    
    def __str__(self):
        label_str = f" ({self.label})" if self.label else ""
        return f"{self.recipient_name}{label_str} — {self.city}, {self.country}"
    
    def save(self, *args, **kwargs):
        # If this is set as default, unset other defaults for this user
        if self.is_default:
            SavedAddress.objects.filter(
                user=self.user, is_default=True
            ).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)
