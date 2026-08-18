"""Model fields for values that must not sit in the database as plaintext."""

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


def _fernet():
    return Fernet(settings.FIELD_ENCRYPTION_KEY)


def encrypt_value(value):
    """Encrypt a plaintext string the same way EncryptedCharField does.

    Exposed for callers (e.g. cache layers) that need to hold a value
    somewhere other than this DB column without storing it as plaintext.
    """
    if not value:
        return value
    return _fernet().encrypt(value.encode()).decode()


def decrypt_value(value):
    """Inverse of encrypt_value(). Passes through pre-encryption plaintext."""
    if not value:
        return value
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return value


class EncryptedCharField(models.CharField):
    """A CharField encrypted at rest with Fernet (AES-128-CBC + HMAC).

    Encryption/decryption happens in Django, not Postgres — a DB dump,
    backup, or direct SQL read only ever sees ciphertext. Blank values
    pass through unencrypted so blank=True fields behave normally.

    max_length validates the PLAINTEXT (form input); the DB column is
    sized larger to fit the base64 Fernet ciphertext, which runs longer
    than the plaintext (~35-45% expansion plus ~57 bytes of fixed
    overhead) -- doubling max_length plus a fixed buffer comfortably
    covers it.
    """

    def db_type(self, connection):
        ciphertext_max_length = self.max_length * 2 + 100
        return 'varchar(%s)' % ciphertext_max_length

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return encrypt_value(value)

    def from_db_value(self, value, expression, connection):
        # decrypt_value() passes through pre-encryption plaintext rows
        # that haven't been migrated yet, via InvalidToken.
        return decrypt_value(value)
