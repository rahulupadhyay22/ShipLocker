"""Sanitizes admin-authored HTML (StaticPage.content, PageSection.content)
before render. These fields are superuser-editable free-form HTML by design
(see apps/content/models.py help_text) -- edit access is superuser-only
today, but sanitizing at render time keeps this safe even if that access
control ever loosens, rather than trusting the write side forever."""

import bleach
from django import template
from django.utils.safestring import mark_safe

register = template.Library()

ALLOWED_TAGS = [
    'p', 'br', 'hr', 'span', 'div',
    'strong', 'b', 'em', 'i', 'u', 's',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li', 'blockquote', 'a',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
]
ALLOWED_ATTRIBUTES = {
    'a': ['href', 'title', 'target', 'rel'],
    '*': ['class'],
}


@register.filter(name='sanitize_html')
def sanitize_html(value):
    if not value:
        return value
    cleaned = bleach.clean(
        value, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES, strip=True,
    )
    return mark_safe(cleaned)
