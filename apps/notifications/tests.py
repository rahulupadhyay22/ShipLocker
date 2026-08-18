from django.core.cache import cache
from django.db import connection
from django.test import TestCase

from apps.notifications.models import AppSettings


class EncryptedServiceRoleKeyTests(TestCase):
    """supabase_service_role_key must never sit in the DB as plaintext."""

    def test_stored_value_is_encrypted_but_reads_back_as_plaintext(self):
        # get_or_create (not get_settings()) — get_settings() caches the
        # instance process-wide, which can outlive this test's transaction.
        settings_obj, _ = AppSettings.objects.get_or_create(pk=1)
        settings_obj.supabase_service_role_key = 'a-fake-service-role-key'
        settings_obj.save(update_fields=['supabase_service_role_key'])

        with connection.cursor() as cur:
            cur.execute(
                'SELECT supabase_service_role_key FROM notifications_appsettings WHERE id = %s',
                [settings_obj.id],
            )
            raw_value = cur.fetchone()[0]

        self.assertNotEqual(raw_value, 'a-fake-service-role-key')
        self.assertTrue(raw_value.startswith('gAAAAA'))  # Fernet token prefix

        reloaded = AppSettings.objects.get(pk=settings_obj.pk)
        self.assertEqual(reloaded.supabase_service_role_key, 'a-fake-service-role-key')

    def test_blank_value_is_not_encrypted(self):
        settings_obj, _ = AppSettings.objects.get_or_create(pk=1)
        settings_obj.supabase_service_role_key = ''
        settings_obj.save(update_fields=['supabase_service_role_key'])

        reloaded = AppSettings.objects.get(pk=settings_obj.pk)
        self.assertEqual(reloaded.supabase_service_role_key, '')


class GetSettingsCacheTests(TestCase):
    """get_settings() must return the real key, but never cache it in plaintext."""

    def tearDown(self):
        cache.delete('app_settings')

    def test_cached_copy_stores_ciphertext_not_plaintext(self):
        settings_obj, _ = AppSettings.objects.get_or_create(pk=1)
        settings_obj.supabase_service_role_key = 'a-fake-service-role-key'
        settings_obj.save(update_fields=['supabase_service_role_key'])
        cache.delete('app_settings')  # save() already does this; be explicit

        fetched = AppSettings.get_settings()
        self.assertEqual(fetched.supabase_service_role_key, 'a-fake-service-role-key')

        cached = cache.get('app_settings')
        self.assertNotEqual(cached.supabase_service_role_key, 'a-fake-service-role-key')
        self.assertTrue(cached.supabase_service_role_key.startswith('gAAAAA'))

    def test_cache_hit_still_returns_decrypted_key(self):
        settings_obj, _ = AppSettings.objects.get_or_create(pk=1)
        settings_obj.supabase_service_role_key = 'a-fake-service-role-key'
        settings_obj.save(update_fields=['supabase_service_role_key'])

        AppSettings.get_settings()  # populates the cache
        fetched_again = AppSettings.get_settings()  # now a cache hit

        self.assertEqual(fetched_again.supabase_service_role_key, 'a-fake-service-role-key')
