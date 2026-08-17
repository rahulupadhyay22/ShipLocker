"""Django signals driving the shipment-batch storage model from Parcel
lifecycle events. See apps/locker/services/batch_billing.py for the actual
billing logic — this module is deliberately thin, mapping Parcel status
transitions onto batch_billing calls exactly once, in one place, per the
storage-fee spec's Rules section."""

import logging

from django.db import IntegrityError
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Parcel

logger = logging.getLogger(__name__)

# "Physically in warehouse" — must match apps/locker/services/batch_billing.py.
IN_WAREHOUSE_STATUSES = {'pending', 'action_required', 'approved'}


# Track original status before save (same shape as apps/shipments/signals.py's
# _original_tracking_numbers) so a parcel saved twice with the same status
# doesn't double-count.
_original_statuses = {}


@receiver(pre_save, sender=Parcel)
def store_original_status(sender, instance, **kwargs):
    if instance.pk:
        try:
            _original_statuses[instance.pk] = Parcel.objects.get(pk=instance.pk).status
        except Parcel.DoesNotExist:
            _original_statuses[instance.pk] = None
    else:
        _original_statuses[instance.pk] = None


def _handle_parcel_entered_warehouse(parcel, today):
    """A parcel just became 'physically in warehouse' — either it's brand
    new, or its status transitioned back into that set. Joins the locker's
    open batch if one exists, otherwise opens a new one. Races against a
    concurrent parcel-intake request creating the same locker's first batch
    are resolved by catching the unique_open_batch_per_locker IntegrityError
    and rejoining the batch that won, rather than letting the exception
    propagate and 500 the request that triggered this signal."""
    from .services import batch_billing

    batch = batch_billing.get_open_batch(parcel.locker)
    if batch is not None:
        batch_billing.add_parcel_to_batch(batch, today)
        return

    try:
        batch_billing.create_batch(parcel.locker, today)
    except IntegrityError:
        batch = batch_billing.get_open_batch(parcel.locker)
        if batch is not None:
            batch_billing.add_parcel_to_batch(batch, today)
        else:
            # Extremely unlikely: the race winner's batch closed again
            # between the IntegrityError and this re-fetch. Log and let the
            # next parcel-intake event open a fresh batch — never raise out
            # of a signal handler for a case this transient.
            logger.warning(
                f"Batch race rejoin found no open batch for locker={parcel.locker.locker_id}; "
                f"a future parcel event will open a new one."
            )


def _handle_parcel_left_warehouse(parcel, today):
    """A parcel just stopped being 'physically in warehouse' (shipped,
    returned, or discarded). Decrements the locker's open batch, closing it
    if the count hits 0."""
    from .services import batch_billing

    batch = batch_billing.get_open_batch(parcel.locker)
    if batch is not None:
        batch_billing.remove_parcel_from_batch(batch, today)
    else:
        logger.warning(
            f"Parcel {parcel.display_id} left the warehouse but locker="
            f"{parcel.locker.locker_id} has no open batch to decrement."
        )


@receiver(post_save, sender=Parcel)
def sync_batch_on_status_change(sender, instance, created, **kwargs):
    today = timezone.localdate()

    if created:
        # A brand-new Parcel has no "before" state to diff against — the
        # pre_save snapshot lookup above found nothing (Parcel.DoesNotExist).
        # Treat this as an explicit, separate transition rather than
        # inferring one from a diff. Parcel.status defaults to 'pending', so
        # any other initial status here is not expected and is a no-op.
        _original_statuses.pop(instance.pk, None)
        if instance.status in IN_WAREHOUSE_STATUSES:
            _handle_parcel_entered_warehouse(instance, today)
        return

    original_status = _original_statuses.pop(instance.pk, None)
    if original_status == instance.status:
        return  # no real transition — e.g. an unrelated field was updated

    was_in_warehouse = original_status in IN_WAREHOUSE_STATUSES
    now_in_warehouse = instance.status in IN_WAREHOUSE_STATUSES

    if not was_in_warehouse and now_in_warehouse:
        _handle_parcel_entered_warehouse(instance, today)
    elif was_in_warehouse and not now_in_warehouse:
        _handle_parcel_left_warehouse(instance, today)
    # else: transition stayed entirely inside or entirely outside the
    # warehouse set (e.g. action_required -> approved) — no batch effect.
