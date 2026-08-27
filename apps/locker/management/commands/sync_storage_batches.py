"""
Management command to run the daily storage-batch billing job.

Usage:
    python manage.py sync_storage_batches             # bill all open batches
    python manage.py sync_storage_batches --dry-run    # show what would happen, no writes

Schedule to run once per day (see apps/locker/README_STORAGE_BILLING.md for
the scheduler-wiring note — this must actually be invoked daily by an
external scheduler; the command existing here is not sufficient on its own):
    0 2 * * * cd /path/to/project && python manage.py sync_storage_batches
"""

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.locker.models import Batch
from apps.locker.services import batch_billing

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Run one day of batch storage billing (Batch.active_free/active_chargeable)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would happen without saving',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        today = timezone.localdate()

        queryset = Batch.objects.filter(
            batch_status__in=['active_free', 'active_chargeable']
        ).select_related('locker')
        total = queryset.count()
        charged = 0
        skipped_already_billed = 0
        transitioned = 0
        closed = 0
        errors = 0

        self.stdout.write(f"Found {total} open batches to process for {today}...")

        for batch in queryset:
            try:
                if dry_run:
                    self.stdout.write(f"  Would process batch {batch.id} (locker={batch.locker.locker_id})")
                    continue

                was_status = batch.batch_status
                was_count = batch.current_parcel_count
                charge = batch_billing.run_daily_billing(batch, today)

                if was_count == 0:
                    closed += 1
                    self.stdout.write(f"  ✓ {batch.locker.locker_id}: closed (0 parcels)")
                elif charge is not None:
                    charged += 1
                    self.stdout.write(f"  ✓ {batch.locker.locker_id}: charged ₹{charge.amount}")
                elif was_status == 'active_free' and batch_billing.get_open_batch(batch.locker) is not None:
                    transitioned += 1
                else:
                    skipped_already_billed += 1
            except Exception as e:
                errors += 1
                logger.error(f"Error running daily billing for batch {batch.id}: {e}")
                self.stdout.write(self.style.ERROR(f"  ✗ {batch.id}: {str(e)}"))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Storage billing sync complete!"))
        self.stdout.write(f"  Total open batches: {total}")
        self.stdout.write(f"  Charges created: {charged}")
        self.stdout.write(f"  Batches closed (0 parcels): {closed}")
        self.stdout.write(f"  Skipped (still free, or already billed today): {skipped_already_billed + transitioned}")
        self.stdout.write(f"  Errors: {errors}")

        if dry_run:
            self.stdout.write(self.style.WARNING("  (Dry run — no changes saved)"))
