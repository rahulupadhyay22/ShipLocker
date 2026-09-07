"""
Management command to mark abandoned pending payments as failed.

CreatePaymentOrderView only treats a pending Payment as "reusable" for 30
minutes (apps/payments/views.py) and the Razorpay order itself now carries a
matching expire_by (apps/payments/services.py), but neither of those actually
touch the Payment row in our own database -- a payment nobody completes stays
status='pending' forever otherwise. This sweeps those up.

Usage:
    python manage.py expire_stale_payments             # mark stale pending payments failed
    python manage.py expire_stale_payments --dry-run    # show what would happen, no writes

Not currently wired to any scheduler -- same gap as sync_storage_batches and
sync_tracking (see apps/locker/README_STORAGE_BILLING.md). Run periodically
via an external cron, e.g.:
    */15 * * * * cd /path/to/project && python manage.py expire_stale_payments
"""

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from apps.payments.models import Payment
from apps.payments.services import ORDER_EXPIRY_SECONDS

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Mark pending payments older than the order-expiry window as failed'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would happen without saving',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        cutoff = timezone.now() - timedelta(seconds=ORDER_EXPIRY_SECONDS)

        stale = Payment.objects.filter(status='pending', created_at__lt=cutoff)
        total = stale.count()
        self.stdout.write(f"Found {total} stale pending payment(s) older than {cutoff}...")

        if dry_run:
            for payment in stale:
                self.stdout.write(f"  Would expire {payment.display_id or payment.pk}")
            self.stdout.write(self.style.WARNING("  (Dry run — no changes saved)"))
            return

        updated = stale.update(status='failed', failure_reason='Payment window expired without completion')
        self.stdout.write(self.style.SUCCESS(f"Expired {updated} stale pending payment(s)."))
