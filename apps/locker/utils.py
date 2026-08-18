"""File upload utilities for Supabase Storage (Private Buckets).

All 3 buckets are PRIVATE for security:
- parcel-images: Inspection photos
- invoices: Purchase invoices  
- kyc-documents: Identity documents

Files are accessed via SIGNED URLs that expire after 1 hour.
"""

import uuid
import os
import logging
from django.core.files.uploadedfile import UploadedFile
from django.core.cache import cache
from apps.accounts.services import SupabaseStorage

logger = logging.getLogger('security')


def get_user_locker_id(user) -> str:
    """Get user's locker_id for folder organization.
    
    Falls back to user UUID if no locker exists.
    """
    try:
        if hasattr(user, 'locker') and user.locker:
            return user.locker.locker_id
    except Exception:
        pass
    # Fallback to user ID (UUID)
    return str(user.id)


# =============================================================================
# UPLOAD FUNCTIONS - Return file path (not URL since buckets are private)
# =============================================================================

def upload_invoice(file: UploadedFile, locker_id: str, parcel_display_id: str, is_personal_shop: bool = False) -> str:
    """Upload invoice to private Supabase Storage.

    Storage structure: {my-trunk|personal-shopper}/{locker_id}/{parcel_display_id}/invoice.ext
    Fixed filename (no random suffix): re-uploading a new invoice for the same
    parcel overwrites the old one at the same path, since Parcel.invoice_url
    only ever points at one file — a random suffix would just leave the
    previous upload as an orphan nothing references anymore.

    Args:
        file: The uploaded file
        locker_id: User's locker ID (e.g., 'RB-12345')
        parcel_display_id: Parcel display ID (e.g., 'RB-12345-P001')
        is_personal_shop: True if this parcel originated from a TrunkAssist
            (PersonalShopRequest) purchase — routes into personal-shopper/
            instead of my-trunk/.

    Returns:
        File path in storage (use get_signed_invoice_url to access)
    """
    storage = SupabaseStorage()
    ext = os.path.splitext(file.name)[1].lower()
    folder = 'personal-shopper' if is_personal_shop else 'my-trunk'
    filename = f"{folder}/{locker_id}/{parcel_display_id}/invoice{ext}"

    storage.upload_file(
        bucket_name='invoices',
        file_path=filename,
        file_data=file.read(),
        content_type=file.content_type,
        upsert=True
    )
    return filename  # Return path, not URL


def upload_parcel_image(file: UploadedFile, locker_id: str, parcel_display_id: str, is_primary: bool = False) -> str:
    """Upload parcel inspection image to private storage.
    
    Compresses image to max 1920x1080 and 80% quality to reduce size.
    
    Args:
        file: The uploaded file
        locker_id: User's locker ID (e.g., 'RB-12345')
        parcel_display_id: Parcel display ID (e.g., 'RB-12345-P001')
        is_primary: Whether this is the primary image
    
    Storage structure: my-trunk/{locker_id}/{parcel_display_id}/{image_name}.ext
    Example: my-trunk/RB-12345/RB-12345-P001/primary_abc123.jpg

    Returns:
        File path in storage (use get_signed_parcel_image_url to access)
    """
    storage = SupabaseStorage()
    ext = os.path.splitext(file.name)[1].lower()
    prefix = "primary" if is_primary else "photo"
    # Organized by: my-trunk/locker_id/parcel_display_id/image_name.ext
    filename = f"my-trunk/{locker_id}/{parcel_display_id}/{prefix}_{uuid.uuid4().hex[:6]}{ext}"
    
    # Compress image if it's an image file
    file_data = file.read()
    content_type = file.content_type
    
    if ext in ['.jpg', '.jpeg', '.png', '.webp']:
        try:
            from PIL import Image, ImageOps
            import io
            
            img = Image.open(io.BytesIO(file_data))
            
            # Convert to RGB (in case of RGBA/P)
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            
            # Auto-orient based on EXIF tag
            img = ImageOps.exif_transpose(img)
            
            # Resize if too large (max 1920x1920)
            img.thumbnail((1920, 1920), Image.Resampling.LANCZOS)
            
            # Save to buffer with compression
            output = io.BytesIO()
            img.save(output, format='JPEG', quality=80, optimize=True)
            file_data = output.getvalue()
            content_type = 'image/jpeg'
            filename = os.path.splitext(filename)[0] + '.jpg'  # Force jpg extension
        except Exception as e:
            logger.error(f"Image compression failed, uploading original: {e}")
    
    storage.upload_file(
        bucket_name='parcel-images',
        file_path=filename,
        file_data=file_data,
        content_type=content_type
    )
    return filename  # Return path, not URL


def upload_kyc_document(file: UploadedFile, locker_id: str, doc_type: str) -> str:
    """Upload KYC document to private storage.
    
    Args:
        file: The uploaded file
        locker_id: User's locker ID (e.g., 'RB-12345') for folder organization
        doc_type: Type of KYC document (aadhaar, passport, etc.)
    
    Returns:
        File path in storage (use get_signed_kyc_url to access)
    """
    storage = SupabaseStorage()
    ext = os.path.splitext(file.name)[1].lower()
    # Simple path: locker_id folder with descriptive filename
    filename = f"{locker_id}/{doc_type}_{uuid.uuid4().hex[:8]}{ext}"
    
    storage.upload_file(
        bucket_name='kyc-documents',
        file_path=filename,
        file_data=file.read(),
        content_type=file.content_type
    )
    return filename


def upload_shipment_document(file: UploadedFile, locker_id: str, shipment_display_id: str, doc_type: str) -> str:
    """Upload shipment document to private storage.
    
    Args:
        file: The uploaded file
        locker_id: User's locker ID (e.g., 'RB-12345')
        shipment_display_id: Shipment display ID (e.g., 'RB-12345-S001')
        doc_type: Type of document (declaration, invoice, etc.)
    
    Example path: shipment/RB-12345/customs_RB-12345-S001_abc123.pdf
    """
    storage = SupabaseStorage()
    ext = os.path.splitext(file.name)[1].lower()
    # Path: shipment/{locker_id}/{doc_type}_{shipment_display_id}_{unique}.ext
    filename = f"shipment/{locker_id}/{doc_type}_{shipment_display_id}_{uuid.uuid4().hex[:6]}{ext}"
    
    storage.upload_file(
        bucket_name='invoices',
        file_path=filename,
        file_data=file.read(),
        content_type=file.content_type
    )
    return filename


# =============================================================================
# SIGNED URL FUNCTIONS - Generate temporary URLs for private file access
# 
# Expiration times:
# - Parcel images/invoices: 7 days (604800 seconds) - for shipping duration
# - KYC documents: 24 hours (86400 seconds) - more sensitive
# - Shipment docs: 7 days (604800 seconds) - for shipping duration
# =============================================================================

# Time constants
SEVEN_DAYS = 604800      # 7 days in seconds
TWENTY_FOUR_HOURS = 86400  # 24 hours in seconds


def get_signed_url(bucket_name: str, file_path: str, expires_in: int = 3600) -> str:
    """Get temporary signed URL for any private bucket file.
    
    Generic function for all buckets. Results are cached to avoid
    repeated Supabase API calls. Cache TTL = expires_in minus 1 hour buffer.
    
    Args:
        bucket_name: Name of the Supabase storage bucket
        file_path: Path to file in the bucket
        expires_in: URL expiry in seconds (default 1 hour)
    
    Returns:
        Signed URL string or empty string on error
    """
    if not file_path:
        return ''
    cache_key = f'signed_url:{bucket_name}:{file_path}'
    url = cache.get(cache_key)
    if url:
        return url
    try:
        storage = SupabaseStorage()
        result = storage.get_signed_url(bucket_name, file_path, expires_in)
        url = result.get('signedURL', '') if isinstance(result, dict) else str(result)
        if url:
            cache_ttl = max(expires_in - 3600, 3600)  # At least 1 hour, minus 1 hour buffer
            cache.set(cache_key, url, cache_ttl)
        return url
    except Exception as e:
        logger.warning(f'Signed URL generation failed for {bucket_name}/{file_path}: {e}')
        return ''


def get_signed_invoice_url(file_path: str, expires_in: int = SEVEN_DAYS) -> str:
    """Get temporary signed URL for private invoice.
    
    Default expiry: 7 days (for shipping duration).
    Results are cached with a 1-hour safety buffer before expiry.
    """
    if not file_path:
        return ''
    cache_key = f'signed_url:invoices:{file_path}'
    url = cache.get(cache_key)
    if url:
        return url
    try:
        storage = SupabaseStorage()
        result = storage.get_signed_url('invoices', file_path, expires_in)
        url = result.get('signedURL', '') if isinstance(result, dict) else str(result)
        if url:
            cache.set(cache_key, url, max(expires_in - 3600, 3600))
        return url
    except Exception as e:
        logger.warning(f'Signed invoice URL failed for {file_path}: {e}')
        return ''


def get_signed_parcel_image_url(file_path: str, expires_in: int = SEVEN_DAYS) -> str:
    """Get temporary signed URL for private parcel image.
    
    Default expiry: 7 days (for shipping duration).
    Results are cached with a 1-hour safety buffer before expiry.
    """
    if not file_path:
        return ''
    cache_key = f'signed_url:parcel-images:{file_path}'
    url = cache.get(cache_key)
    if url:
        return url
    try:
        storage = SupabaseStorage()
        result = storage.get_signed_url('parcel-images', file_path, expires_in)
        url = result.get('signedURL', '') if isinstance(result, dict) else str(result)
        if url:
            cache.set(cache_key, url, max(expires_in - 3600, 3600))
        return url
    except Exception as e:
        logger.warning(f'Signed parcel image URL failed for {file_path}: {e}')
        return ''


def get_signed_kyc_url(file_path: str, expires_in: int = TWENTY_FOUR_HOURS) -> str:
    """Get temporary signed URL for private KYC document.
    
    Default expiry: 24 hours (sensitive documents).
    Results are cached with a 1-hour safety buffer before expiry.
    """
    if not file_path:
        return ''
    cache_key = f'signed_url:kyc-documents:{file_path}'
    url = cache.get(cache_key)
    if url:
        return url
    try:
        storage = SupabaseStorage()
        result = storage.get_signed_url('kyc-documents', file_path, expires_in)
        url = result.get('signedURL', '') if isinstance(result, dict) else str(result)
        if url:
            cache.set(cache_key, url, max(expires_in - 3600, 3600))
        return url
    except Exception as e:
        logger.warning(f'Signed KYC URL failed for {file_path}: {e}')
        return ''


def get_signed_shipment_doc_url(file_path: str, expires_in: int = SEVEN_DAYS) -> str:
    """Get temporary signed URL for private shipment document.
    
    Default expiry: 7 days (for shipping duration).
    Results are cached with a 1-hour safety buffer before expiry.
    """
    if not file_path:
        return ''
    cache_key = f'signed_url:invoices:{file_path}'
    url = cache.get(cache_key)
    if url:
        return url
    try:
        storage = SupabaseStorage()
        result = storage.get_signed_url('invoices', file_path, expires_in)
        url = result.get('signedURL', '') if isinstance(result, dict) else str(result)
        if url:
            cache.set(cache_key, url, max(expires_in - 3600, 3600))
        return url
    except Exception as e:
        logger.warning(f'Signed shipment doc URL failed for {file_path}: {e}')
        return ''

