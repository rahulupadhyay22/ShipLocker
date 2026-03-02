"""Security validators for IndiaBox."""

import re
import os
from django.core.exceptions import ValidationError
from django.conf import settings


def validate_file_upload(file):
    """Validate uploaded file for security.
    
    Checks:
    - File size limit
    - Allowed content types
    - File extension matches content type
    """
    # Check file size
    max_size = getattr(settings, 'MAX_UPLOAD_SIZE', 5 * 1024 * 1024)
    if file.size > max_size:
        raise ValidationError(f'File too large. Maximum size is {max_size // (1024*1024)}MB.')
    
    # Check content type
    allowed_types = getattr(settings, 'ALLOWED_UPLOAD_TYPES', [
        'application/pdf',
        'image/jpeg',
        'image/png',
        'image/jpg',
    ])
    
    if file.content_type not in allowed_types:
        raise ValidationError(
            f'Invalid file type: {file.content_type}. '
            f'Allowed types: PDF, JPEG, PNG.'
        )
    
    # Check file extension matches content type
    ext = os.path.splitext(file.name)[1].lower()
    valid_extensions = {
        'application/pdf': ['.pdf'],
        'image/jpeg': ['.jpg', '.jpeg'],
        'image/png': ['.png'],
        'image/jpg': ['.jpg', '.jpeg'],
    }
    
    allowed_exts = valid_extensions.get(file.content_type, [])
    if ext not in allowed_exts:
        raise ValidationError(
            f'File extension {ext} does not match content type {file.content_type}.'
        )
    
    return True


def sanitize_filename(filename):
    """Sanitize filename to prevent path traversal and special characters."""
    # Remove path components
    filename = os.path.basename(filename)
    
    # Remove special characters, keep only alphanumeric, dash, underscore, dot
    sanitized = re.sub(r'[^\w\-\.]', '_', filename)
    
    # Prevent hidden files
    if sanitized.startswith('.'):
        sanitized = '_' + sanitized[1:]
    
    # Limit length
    if len(sanitized) > 100:
        name, ext = os.path.splitext(sanitized)
        sanitized = name[:96] + ext
    
    return sanitized


def validate_email(email):
    """Validate email format strictly."""
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        raise ValidationError('Invalid email format.')
    
    # Check for dangerous characters
    dangerous_chars = ['<', '>', '"', "'", '\\', '\n', '\r']
    if any(char in email for char in dangerous_chars):
        raise ValidationError('Email contains invalid characters.')
    
    return True


def validate_phone(phone):
    """Validate phone number format."""
    # Remove common separators
    cleaned = re.sub(r'[\s\-\(\)\.]', '', phone)
    
    # Must start with + or digit, contain only digits after
    if not re.match(r'^\+?\d{7,15}$', cleaned):
        raise ValidationError('Invalid phone number format.')
    
    return True


def validate_tracking_number(tracking):
    """Validate tracking number format."""
    # Alphanumeric, some hyphens allowed
    if not re.match(r'^[A-Za-z0-9\-]{5,40}$', tracking):
        raise ValidationError('Invalid tracking number format.')
    
    # No script injection
    if '<' in tracking or '>' in tracking:
        raise ValidationError('Tracking number contains invalid characters.')
    
    return True


def sanitize_text_input(text, max_length=500):
    """Sanitize text input to prevent XSS."""
    if not text:
        return text
    
    # Limit length
    text = text[:max_length]
    
    # Remove dangerous HTML
    dangerous_patterns = [
        r'<script[^>]*>.*?</script>',
        r'javascript:',
        r'on\w+\s*=',
        r'<iframe',
        r'<object',
        r'<embed',
    ]
    
    for pattern in dangerous_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    
    return text


def validate_address(address_dict):
    """Validate address fields."""
    required = ['recipient_name', 'address_line1', 'city', 'country', 'postal_code']
    
    for field in required:
        value = address_dict.get(field, '')
        if not value or len(value.strip()) < 2:
            raise ValidationError(f'{field.replace("_", " ").title()} is required.')
        
        # Sanitize each field
        address_dict[field] = sanitize_text_input(value, max_length=200)
    
    return address_dict
