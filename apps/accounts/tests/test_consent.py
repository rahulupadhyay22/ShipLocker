from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import ConsentRecord, User


class LoginConsentCheckboxTests(TestCase):
    def setUp(self):
        # /accounts/login/ and /accounts/login/google/ share a rate-limit
        # bucket (RateLimitMiddleware matches by path prefix, and
        # '/accounts/login/' is a prefix of the google path too) — clear
        # between tests so this class's own POSTs don't trip it.
        cache.clear()

    @patch('apps.accounts.views.SupabaseAuth.sign_in_with_otp')
    def test_missing_consent_blocks_otp_send(self, mock_sign_in):
        response = self.client.post(reverse('accounts:login'), {'email': 'new@example.com'})

        self.assertEqual(response.status_code, 200)
        mock_sign_in.assert_not_called()
        self.assertNotIn('signup_consent_given', self.client.session)

    @patch('apps.accounts.views.SupabaseAuth.sign_in_with_otp')
    def test_consent_checked_sends_otp_and_stashes_session_flag(self, mock_sign_in):
        response = self.client.post(reverse('accounts:login'), {
            'email': 'new@example.com', 'privacy_consent': 'on',
        })

        self.assertRedirects(response, reverse('accounts:verify_otp'))
        mock_sign_in.assert_called_once()
        self.assertTrue(self.client.session['signup_consent_given'])


class VerifyOTPConsentRecordTests(TestCase):
    def setUp(self):
        cache.clear()

    def _start_login(self, email='newuser@example.com'):
        with patch('apps.accounts.views.SupabaseAuth.sign_in_with_otp'):
            self.client.post(reverse('accounts:login'), {'email': email, 'privacy_consent': 'on'})
        session = self.client.session
        return session['otp_session_token']

    @patch('apps.accounts.views.SupabaseAuth.verify_otp')
    def test_new_account_creation_writes_consent_record(self, mock_verify):
        token = self._start_login('newuser@example.com')
        result = MagicMock()
        result.user.id = 'sb-new-1'
        mock_verify.return_value = result

        self.client.post(reverse('accounts:verify_otp'), {'otp': '123456', 'otp_session_token': token})

        user = User.objects.get(email='newuser@example.com')
        record = ConsentRecord.objects.get(user=user)
        self.assertEqual(record.consent_type, 'signup')
        self.assertTrue(record.policy_version)

    @patch('apps.accounts.views.SupabaseAuth.verify_otp')
    def test_returning_user_login_does_not_duplicate_consent_record(self, mock_verify):
        user = User.objects.create(email='returning@example.com', supabase_id='sb-ret-1')
        from apps.accounts.models import Locker
        Locker.objects.create(user=user)
        ConsentRecord.objects.create(user=user, consent_type='signup', policy_version='1.0')

        token = self._start_login('returning@example.com')
        result = MagicMock()
        result.user.id = 'sb-ret-1'
        mock_verify.return_value = result

        self.client.post(reverse('accounts:verify_otp'), {'otp': '123456', 'otp_session_token': token})

        self.assertEqual(ConsentRecord.objects.filter(user=user).count(), 1)
