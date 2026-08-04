import secrets
import uuid
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
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='locker')
    locker_id = models.CharField(max_length=20, unique=True, default=generate_locker_id)
    is_active = models.BooleanField(default=True, help_text="Whether this locker is active")
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
        return f"{self.locker_id.lower()}@indiabox.com"
    
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
