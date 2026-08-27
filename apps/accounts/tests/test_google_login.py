from unittest.mock import patch, MagicMock

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User, Locker, ConsentRecord


class GoogleCallbackViewTests(TestCase):
    """The OAuth code exchange must actually establish a Django session."""

    def _fake_result(self, email='newuser@example.com', supabase_id='sb-123'):
        result = MagicMock()
        result.user.email = email
        result.user.id = supabase_id
        return result

    def _set_pending_verifier(self, verifier='verifier-abc', consent_given=True):
        session = self.client.session
        session['google_code_verifier'] = verifier
        if consent_given:
            session['signup_consent_given'] = True
            session['signup_consent_ip'] = '127.0.0.1'
        session.save()

    @patch('apps.accounts.views.SupabaseAuth.exchange_code_for_session')
    def test_new_user_is_created_and_logged_in(self, mock_exchange):
        self._set_pending_verifier()
        mock_exchange.return_value = self._fake_result()

        response = self.client.get(reverse('accounts:google_callback'), {'code': 'auth-code'})

        self.assertRedirects(response, reverse('accounts:dashboard'))
        mock_exchange.assert_called_once_with('auth-code', 'verifier-abc')
        user = User.objects.get(email='newuser@example.com')
        self.assertEqual(user.supabase_id, 'sb-123')
        self.assertTrue(Locker.objects.filter(user=user).exists())
        self.assertIn('_auth_user_id', self.client.session)
        consent = ConsentRecord.objects.get(user=user)
        self.assertEqual(consent.consent_type, 'signup')
        self.assertEqual(consent.ip_address, '127.0.0.1')

    @patch('apps.accounts.views.SupabaseAuth.exchange_code_for_session')
    def test_existing_user_is_logged_in_without_duplicate_locker(self, mock_exchange):
        self._set_pending_verifier()
        user = User.objects.create(email='existing@example.com', supabase_id='sb-456')
        Locker.objects.create(user=user)
        mock_exchange.return_value = self._fake_result(email='existing@example.com', supabase_id='sb-456')

        response = self.client.get(reverse('accounts:google_callback'), {'code': 'auth-code'})

        self.assertRedirects(response, reverse('accounts:dashboard'))
        self.assertEqual(Locker.objects.filter(user=user).count(), 1)

    def test_missing_code_redirects_to_login_without_calling_supabase(self):
        self._set_pending_verifier()
        response = self.client.get(reverse('accounts:google_callback'))

        self.assertRedirects(response, reverse('accounts:login'))
        self.assertNotIn('_auth_user_id', self.client.session)

    @patch('apps.accounts.views.SupabaseAuth.exchange_code_for_session')
    def test_missing_session_verifier_redirects_to_login_without_calling_supabase(self, mock_exchange):
        # e.g. the callback is replayed, or the session cookie didn't round-trip.
        response = self.client.get(reverse('accounts:google_callback'), {'code': 'auth-code'})

        self.assertRedirects(response, reverse('accounts:login'))
        mock_exchange.assert_not_called()

    def test_provider_error_redirects_to_login(self):
        response = self.client.get(reverse('accounts:google_callback'), {'error': 'access_denied'})

        self.assertRedirects(response, reverse('accounts:login'))

    @patch('apps.accounts.views.SupabaseAuth.exchange_code_for_session')
    def test_exchange_failure_redirects_to_login(self, mock_exchange):
        self._set_pending_verifier()
        mock_exchange.side_effect = Exception('boom')

        response = self.client.get(reverse('accounts:google_callback'), {'code': 'auth-code'})

        self.assertRedirects(response, reverse('accounts:login'))
        self.assertNotIn('_auth_user_id', self.client.session)


class GoogleLoginViewTests(TestCase):
    """The verifier gotrue generates for the authorize URL must reach the session.

    POST-only (with a required consent checkbox) since spec 12 (DPDP consent
    logging) — a bare GET link can't be gated behind server-side validation."""

    def setUp(self):
        # /accounts/login/google/ shares a rate-limit bucket with
        # /accounts/login/ (RateLimitMiddleware matches by path prefix) —
        # clear between tests so this class's own POSTs don't trip it.
        cache.clear()

    @patch('apps.accounts.views.SupabaseAuth.sign_in_with_google')
    def test_redirects_to_oauth_url_and_stores_verifier_in_session(self, mock_sign_in):
        mock_sign_in.return_value = ('https://example.supabase.co/auth/v1/authorize?...', 'verifier-xyz')

        response = self.client.post(reverse('accounts:google_login'), {'privacy_consent': 'on'})

        self.assertRedirects(
            response, 'https://example.supabase.co/auth/v1/authorize?...', fetch_redirect_response=False
        )
        self.assertEqual(self.client.session['google_code_verifier'], 'verifier-xyz')
        self.assertTrue(self.client.session['signup_consent_given'])

    @patch('apps.accounts.views.SupabaseAuth.sign_in_with_google')
    def test_missing_consent_blocks_and_does_not_call_supabase(self, mock_sign_in):
        response = self.client.post(reverse('accounts:google_login'), {})

        self.assertRedirects(response, reverse('accounts:login'))
        mock_sign_in.assert_not_called()

    def test_get_redirects_to_login(self):
        response = self.client.get(reverse('accounts:google_login'))
        self.assertRedirects(response, reverse('accounts:login'))
