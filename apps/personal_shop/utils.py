"""File upload utilities for TrunkAssist reference images.

Reuses the existing private `parcel-images` Supabase bucket (no new bucket)
under a `personal-shopper/` path prefix, and the existing signed-URL helper
in apps.locker.utils.
"""

import uuid
import os
import logging
from django.core.files.uploadedfile import UploadedFile
from apps.accounts.services import SupabaseStorage

logger = logging.getLogger('security')


def upload_personal_shop_image(file: UploadedFile, locker_id: str, request_display_id: str) -> str:
    """Upload a TrunkAssist reference image to the parcel-images bucket.

    Storage structure: personal-shopper/{locker_id}/{request_display_id}/{image_name}.ext

    Returns:
        File path in storage (use apps.locker.utils.get_signed_parcel_image_url to access)
    """
    storage = SupabaseStorage()
    ext = os.path.splitext(file.name)[1].lower()
    filename = f"personal-shopper/{locker_id}/{request_display_id}/photo_{uuid.uuid4().hex[:6]}{ext}"

    file_data = file.read()
    content_type = file.content_type

    if ext in ['.jpg', '.jpeg', '.png', '.webp']:
        try:
            from PIL import Image, ImageOps
            import io

            img = Image.open(io.BytesIO(file_data))
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            img = ImageOps.exif_transpose(img)
            img.thumbnail((1920, 1920), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            img.save(output, format='JPEG', quality=80, optimize=True)
            file_data = output.getvalue()
            content_type = 'image/jpeg'
            filename = os.path.splitext(filename)[0] + '.jpg'
        except Exception as e:
            logger.error(f"Image compression failed, uploading original: {e}")

    storage.upload_file(
        bucket_name='parcel-images',
        file_path=filename,
        file_data=file_data,
        content_type=content_type,
    )
    return filename
