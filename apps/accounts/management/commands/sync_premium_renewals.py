"""
Management command to send Premium renewal reminders, start grace periods,
and auto-downgrade lapsed Premium lockers.

Usage:
    python manage.py sync_premium_renewals             # process all Premium lockers
    python manage.py sync_premium_renewals --dry-run    # show what would happen, no writes

Schedule to run once per day, alongside sync_storage_batches:
    0 2 * * * cd /path/to/project && python manage.py sync_premium_renewals
"""

from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.accounts.models import Locker
from apps.locker.services import batch_billing
from apps.notifications.signals import send_notification


class Command(BaseCommand):
    help = 'Send renewal reminders, start grace periods, and auto-downgrade lapsed Premium lockers.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        today = timezone.localdate()
        queryset = Locker.objects.filter(plan_type='paid', premium_expires_at__isnull=False)
        reminded = entered_grace = downgraded = errors = 0

        for locker in queryset:
            try:
                days_until_expiry = (locker.premium_expires_at - today).days
                if days_until_expiry == 7 and locker.payment_grace_until is None:
                    if not dry_run:
                        # components=[] is a placeholder — once the
                        # 'premium_renewal_reminder' WhatsApp template exists
                        # in WhatsApp Business Manager, replace this with the
                        # variables it actually declares (e.g. a formatted
                        # locker.premium_expires_at), matching the shape an
                        # existing send_notification call site uses. Do not
                        # ship a literal empty list once the template is known.
                        send_notification(locker.user, 'premium_renewal_reminder', components=[])
                    reminded += 1
                elif today >= locker.premium_expires_at and locker.payment_grace_until is None:
                    if not dry_run:
                        batch_billing.enter_grace_period(locker, today)
                    entered_grace += 1
                elif (
                    locker.payment_grace_until is not None
                    and today > timezone.localtime(locker.payment_grace_until).date()
                ):
                    if not dry_run:
                        batch_billing.resolve_grace_period(locker, today, payment_succeeded=False)
                        locker.premium_expires_at = None
                        locker.save(update_fields=['premium_expires_at'])
                    downgraded += 1
            except Exception as e:
                errors += 1
                self.stdout.write(self.style.ERROR(f"  {locker.locker_id}: {e}"))

        self.stdout.write(self.style.SUCCESS(
            f"Reminders: {reminded}, entered grace: {entered_grace}, downgraded: {downgraded}, errors: {errors}"
        ))
        if dry_run:
            self.stdout.write(self.style.WARNING("(Dry run — no changes saved)"))
