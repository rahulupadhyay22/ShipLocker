"""Regression coverage for the admin OTP gate (indiabox/admin_site.py) --
production-readiness audit item: staff/superuser logins to /manage-rb-panel/
must pass 2FA, but unverified staff must still be able to reach the
enrollment (setup) view or nobody could ever complete their first login."""

from django.test import TestCase
from django.contrib.auth import get_user_model
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_static.models import StaticDevice

User = get_user_model()


class OTPAdminGateTest(TestCase):
    def test_anonymous_redirected_to_two_factor_login(self):
        r = self.client.get('/manage-rb-panel/', follow=True)
        self.assertEqual(r.redirect_chain[-1][0].split('?')[0], '/manage-rb-panel/account/login/')
        self.assertEqual(r.status_code, 200)

    def test_staff_without_device_blocked_after_plain_login(self):
        staff = User.objects.create(email='staff-no-otp@example.com', is_staff=True, is_superuser=True)
        self.client.force_login(staff)
        r = self.client.get('/manage-rb-panel/', follow=True)
        self.assertEqual(r.redirect_chain[-1][0].split('?')[0], '/manage-rb-panel/account/login/')
        self.assertEqual(r.status_code, 200)

    def test_staff_with_verified_device_gets_in(self):
        staff = User.objects.create(email='staff-otp@example.com', is_staff=True, is_superuser=True)
        self.client.force_login(staff)
        device = StaticDevice.objects.create(user=staff, name='test', confirmed=True)
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()
        r = self.client.get('/manage-rb-panel/', follow=False)
        self.assertEqual(r.status_code, 200)

    def test_setup_view_reachable_while_unverified(self):
        staff = User.objects.create(email='staff-setup@example.com', is_staff=True, is_superuser=True)
        self.client.force_login(staff)
        r = self.client.get('/manage-rb-panel/account/two_factor/setup/', follow=False)
        self.assertEqual(r.status_code, 200)
