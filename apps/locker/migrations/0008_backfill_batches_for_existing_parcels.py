from django.db import migrations


# "Physically in warehouse" — mirrors the definition in
# apps/locker/services/batch_billing.py and apps/locker/signals.py.
OPEN_PARCEL_STATUSES = ['pending', 'action_required', 'approved']


def backfill_batches(apps, schema_editor):
    """One active_chargeable Batch per locker that already has parcels in
    the warehouse when this migrates. No pass is consumed — these parcels
    predate the pass system, and backdating a pass grant would either
    falsely shrink a user's current-year quota or falsely grant free days
    for storage time that already elapsed under the old flat-30-day rule.
    UserQuota is deliberately untouched by this migration."""
    Locker = apps.get_model('accounts', 'Locker')
    Parcel = apps.get_model('locker', 'Parcel')
    Batch = apps.get_model('locker', 'Batch')

    locker_ids_with_open_parcels = (
        Parcel.objects.filter(status__in=OPEN_PARCEL_STATUSES)
        .values_list('locker_id', flat=True)
        .distinct()
    )

    for locker in Locker.objects.filter(id__in=list(locker_ids_with_open_parcels)):
        open_parcels = Parcel.objects.filter(locker=locker, status__in=OPEN_PARCEL_STATUSES)
        earliest_received = open_parcels.order_by('received_at').values_list('received_at', flat=True).first()
        first_parcel_received_date = earliest_received.date() if earliest_received else None
        if first_parcel_received_date is None:
            continue

        Batch.objects.create(
            locker=locker,
            plan_type_at_creation=locker.plan_type,
            quota_year=first_parcel_received_date.year,
            batch_status='active_chargeable',
            first_parcel_received_date=first_parcel_received_date,
            free_storage_end_date=None,
            current_parcel_count=open_parcels.count(),
        )


def noop_reverse(apps, schema_editor):
    """Not reversible in a meaningful way — reversing would delete Batch
    rows that may have already accrued real BatchCharge history by the
    time anyone runs this in reverse. Deliberately a no-op."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('locker', '0007_userquota_batch'),
    ]

    operations = [
        migrations.RunPython(backfill_batches, noop_reverse),
    ]
