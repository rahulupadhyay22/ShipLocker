import json
import logging

from django.db import transaction

logger = logging.getLogger('security')


def create_return_request(parcel, reason):
    """Creates the ReturnRequest and moves the parcel to 'return_requested'.
    Shared by finalize_return_request (paid path) and
    CreateReturnPaymentOrderView (free path — return_service_charge is
    inactive or computes to 0, same as how a misconfigured consolidation
    fee is treated as 'not charged' rather than 'blocked')."""
    from .. import models

    models.ReturnRequest.objects.create(parcel=parcel, reason=reason)
    parcel.status = 'return_requested'
    parcel.save(update_fields=['status', 'updated_at'])


def finalize_return_request(payment):
    """Called once a 'return_service_charge' Payment is captured (from
    apps/payments/views.py's apply_payment_captured_side_effects) — creates
    the ReturnRequest and moves the parcel to 'return_requested', same as
    the old free RequestReturnView did directly.

    Re-validates the parcel is still eligible: it may have shipped, been
    discarded, or already had a return requested while the Razorpay
    checkout was in flight. If so, the charge stays captured (Razorpay has
    already taken the money) but no request is created — logged loudly so
    staff can issue a manual refund, since there's nothing safe to reverse
    here automatically."""
    from .. import models

    notes = {}
    if payment.notes:
        try:
            notes = json.loads(payment.notes)
        except (TypeError, ValueError):
            notes = {}
    parcel_id = notes.get('parcel_id')
    reason = notes.get('reason', '')

    with transaction.atomic():
        try:
            parcel = models.Parcel.objects.select_for_update().get(
                pk=parcel_id, locker__user=payment.user
            )
        except (models.Parcel.DoesNotExist, ValueError, TypeError):
            logger.error(
                f"Return service charge captured but parcel not found: "
                f"payment={payment.pk} parcel_id={parcel_id!r} — needs manual refund"
            )
            return

        if parcel.status not in ('action_required', 'approved'):
            logger.error(
                f"Return service charge captured but parcel no longer eligible "
                f"(status={parcel.status}): payment={payment.pk} parcel={parcel.pk} "
                f"— needs manual refund"
            )
            return

        create_return_request(parcel, reason)
