import uuid
import random
import string
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
    supabase_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    whatsapp_number = models.CharField(max_length=20, blank=True)
    whatsapp_verified = models.BooleanField(default=False)
    
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


def generate_locker_id():
    """Generate a unique locker ID like RB-12345."""
    prefix = "RB"
    number = ''.join(random.choices(string.digits, k=5))
    return f"{prefix}-{number}"


class Locker(models.Model):
    """Virtual locker assigned to each user."""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='locker')
    locker_id = models.CharField(max_length=20, unique=True, default=generate_locker_id)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'Locker'
        verbose_name_plural = 'Lockers'
    
    def __str__(self):
        return f"{self.locker_id} - {self.user.email}"
    
    @property
    def address(self):
        """Return the common locker address from settings."""
        return settings.LOCKER_ADDRESS
    
    @property
    def email(self):
        """Return formatted locker email."""
        return f"{self.locker_id.lower()}@ruffleberry.com"
    
    @property
    def phone(self):
        """Return warehouse contact phone."""
        return "+91 9876543210"  # Configure in settings


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
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
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
