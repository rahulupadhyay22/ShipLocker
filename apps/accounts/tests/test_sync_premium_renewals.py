from datetime import datetime, timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User, Locker
from apps.locker.models import Batch


def _make_locker(user_email, premium_expires_at=None, payment_grace_until=None):
    user = User.objects.create(email=user_email, is_active=True)
    return Locker.objects.create(
        user=user, plan_type='paid',
        premium_expires_at=premium_expires_at,
        payment_grace_until=payment_grace_until,
    )


class SyncPremiumRenewalsCommandTests(TestCase):
    def test_dry_run_makes_no_db_writes(self):
        today = timezone.localdate()
        locker = _make_locker('dryrun@example.com', premium_expires_at=today + timedelta(days=7))

        with patch('apps.accounts.management.commands.sync_premium_renewals.send_notification') as mock_notify:
            call_command('sync_premium_renewals', '--dry-run')

        mock_notify.assert_not_called()
        locker.refresh_from_db()
        self.assertEqual(locker.premium_expires_at, today + timedelta(days=7))
        self.assertIsNone(locker.payment_grace_until)
        self.assertEqual(locker.plan_type, 'paid')

    def test_reminder_sent_exactly_at_seven_days_out(self):
        today = timezone.localdate()
        locker_due = _make_locker('reminder-due@example.com', premium_expires_at=today + timedelta(days=7))
        _make_locker('reminder-not-due@example.com', premium_expires_at=today + timedelta(days=6))

        with patch('apps.accounts.management.commands.sync_premium_renewals.send_notification') as mock_notify:
            call_command('sync_premium_renewals')

        mock_notify.assert_called_once()
        call_user, call_template = mock_notify.call_args.args
        components = mock_notify.call_args.kwargs['components']
        self.assertEqual(call_user, locker_due.user)
        self.assertEqual(call_template, 'premium_renewal_reminder')
        params = components[0]['parameters']
        self.assertEqual(params[0]['text'], locker_due.user.get_full_name())
        self.assertEqual(params[1]['text'], locker_due.premium_expires_at.strftime('%d %b %Y'))
        self.assertIn('/accounts/subscription/', params[2]['text'])

    def test_reminder_not_sent_when_already_in_grace(self):
        today = timezone.localdate()
        grace_until = timezone.make_aware(datetime.combine(today + timedelta(days=3), datetime.min.time()))
        _make_locker(
            'reminder-grace@example.com', premium_expires_at=today + timedelta(days=7),
            payment_grace_until=grace_until,
        )

        with patch('apps.accounts.management.commands.sync_premium_renewals.send_notification') as mock_notify:
            call_command('sync_premium_renewals')

        mock_notify.assert_not_called()

    def test_expired_locker_enters_grace_period(self):
        today = timezone.localdate()
        locker = _make_locker('enter-grace@example.com', premium_expires_at=today - timedelta(days=1))

        call_command('sync_premium_renewals')

        locker.refresh_from_db()
        self.assertIsNotNone(locker.payment_grace_until)
        self.assertEqual(locker.plan_type, 'paid')  # still paid during grace

    def test_grace_expired_downgrades_and_clears_expiry(self):
        today = timezone.localdate()
        grace_until = timezone.make_aware(datetime.combine(today - timedelta(days=1), datetime.min.time()))
        locker = _make_locker(
            'downgrade@example.com', premium_expires_at=today - timedelta(days=8), payment_grace_until=grace_until,
        )

        call_command('sync_premium_renewals')

        locker.refresh_from_db()
        self.assertEqual(locker.plan_type, 'free')
        self.assertIsNone(locker.premium_expires_at)
        self.assertIsNone(locker.payment_grace_until)

    def test_grace_expired_resolves_pending_batch_as_failure(self):
        today = timezone.localdate()
        grace_until = timezone.make_aware(datetime.combine(today - timedelta(days=1), datetime.min.time()))
        locker = _make_locker(
            'downgrade-batch@example.com', premium_expires_at=today - timedelta(days=8),
            payment_grace_until=grace_until,
        )
        batch = Batch.objects.create(
            locker=locker, plan_type_at_creation='paid', quota_year=today.year,
            batch_status='pending', first_parcel_received_date=today - timedelta(days=5),
            free_storage_end_date=today + timedelta(days=25), current_parcel_count=1,
        )

        call_command('sync_premium_renewals')

        batch.refresh_from_db()
        self.assertNotEqual(batch.batch_status, 'pending')
        self.assertEqual(batch.plan_type_at_creation, 'free')

    def test_outcomes_are_mutually_exclusive_per_locker(self):
        """A locker cannot be both reminded and entered-into-grace in the
        same run — the elif chain guarantees this."""
        today = timezone.localdate()
        _make_locker('reminder-only@example.com', premium_expires_at=today + timedelta(days=7))

        with patch('apps.accounts.management.commands.sync_premium_renewals.send_notification') as mock_notify, \
             patch('apps.accounts.management.commands.sync_premium_renewals.batch_billing.enter_grace_period') as mock_grace:
            call_command('sync_premium_renewals')

        mock_notify.assert_called_once()
        mock_grace.assert_not_called()
