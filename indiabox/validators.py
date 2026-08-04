"""Security validators for IndiaBox."""

import re
import os
from django.core.exceptions import ValidationError
from django.conf import settings


# Magic-byte signatures for the only types this app accepts. The client-sent
# Content-Type header and filename extension are both attacker-controlled
# labels; the file's actual leading bytes are not.
_MAGIC_SIGNATURES = [
    ('application/pdf', b'%PDF-'),
    ('image/jpeg', b'\xff\xd8\xff'),
    ('image/png', b'\x89PNG\r\n\x1a\n'),
]


def _sniff_file_type(file):
    """Return the content type implied by the file's actual bytes, or None."""
    header = file.read(16)
    file.seek(0)
    for content_type, signature in _MAGIC_SIGNATURES:
        if header.startswith(signature):
            return content_type
    return None


def validate_file_upload(file):
    """Validate uploaded file for security.

    Checks:
    - File size limit
    - Allowed content types (as declared)
    - File extension matches declared content type
    - Actual file bytes (magic number) match the declared type — the
      Content-Type header and extension are just attacker-supplied labels
      and are not trusted on their own.
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

    # Check the file's actual bytes match the declared type
    sniffed_type = _sniff_file_type(file)
    if sniffed_type is None:
        raise ValidationError('File content is not a valid PDF, JPEG, or PNG.')

    declared_type = 'image/jpeg' if file.content_type == 'image/jpg' else file.content_type
    if sniffed_type != declared_type:
        raise ValidationError('File content does not match its declared type.')

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


_DANGEROUS_PATTERN = re.compile(
    r'<script[^>]*>|javascript:|on\w+\s*=|<iframe|<object|<embed', re.IGNORECASE
)


def validate_text_input(text, field_name='Field', min_length=1, max_length=500, required=True):
    """Strictly validate free text: type, length, and disallowed markup.

    Rejects invalid input with ValidationError instead of stripping or
    truncating it.
    """
    if text is None:
        text = ''
    if not isinstance(text, str):
        raise ValidationError(f'{field_name} must be text.')

    if not text.strip():
        if required:
            raise ValidationError(f'{field_name} is required.')
        return text

    if len(text) < min_length or len(text) > max_length:
        raise ValidationError(
            f'{field_name} must be between {min_length} and {max_length} characters.'
        )

    if _DANGEROUS_PATTERN.search(text):
        raise ValidationError(f'{field_name} contains disallowed content.')

    return text


def validate_address(address_dict):
    """Validate address fields against a strict schema."""
    field_limits = {
        'recipient_name': 100,
        'address_line1': 200,
        'address_line2': 200,
        'city': 100,
        'state': 100,
        'country': 100,
        'postal_code': 20,
    }
    required = ['recipient_name', 'address_line1', 'city', 'country', 'postal_code']

    for field, max_length in field_limits.items():
        value = address_dict.get(field, '')
        address_dict[field] = validate_text_input(
            value,
            field_name=field.replace('_', ' ').title(),
            min_length=2,
            max_length=max_length,
            required=field in required,
        )

    if 'postal_code' in address_dict and address_dict['postal_code']:
        if not re.match(r'^[A-Za-z0-9\- ]{2,20}$', address_dict['postal_code']):
            raise ValidationError('Postal Code has invalid format.')

    return address_dict
