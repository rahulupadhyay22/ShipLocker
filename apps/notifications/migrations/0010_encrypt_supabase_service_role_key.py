from cryptography.fernet import Fernet
from django.conf import settings
from django.db import migrations


def encrypt_service_role_key(apps, schema_editor):
    AppSettings = apps.get_model('notifications', 'AppSettings')
    fernet = Fernet(settings.FIELD_ENCRYPTION_KEY)
    for row in AppSettings.objects.exclude(supabase_service_role_key=''):
        row.supabase_service_role_key = fernet.encrypt(row.supabase_service_role_key.encode()).decode()
        row.save(update_fields=['supabase_service_role_key'])


def decrypt_service_role_key(apps, schema_editor):
    AppSettings = apps.get_model('notifications', 'AppSettings')
    fernet = Fernet(settings.FIELD_ENCRYPTION_KEY)
    for row in AppSettings.objects.exclude(supabase_service_role_key=''):
        row.supabase_service_role_key = fernet.decrypt(row.supabase_service_role_key.encode()).decode()
        row.save(update_fields=['supabase_service_role_key'])


class Migration(migrations.Migration):
    """Encrypt the existing plaintext value before the field starts
    transparently encrypting/decrypting on every read/write (next migration)
    — otherwise that field class swap would try to decrypt a plaintext row
    and blow up.
    """

    dependencies = [
        ('notifications', '0009_alter_appsettings_support_email'),
    ]

    operations = [
        migrations.RunPython(encrypt_service_role_key, decrypt_service_role_key),
    ]
