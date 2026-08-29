"""Core billing engine for the shipment-batch storage model.

Implements spec 09-storage-fee.md Sections 5-14 (batch creation, mid-batch
top-ups, daily billing, quota management, downgrade/upgrade, grace period,
abandonment clock, 24-hour refund). Every function takes ``today`` as an
explicit ``date`` parameter — nothing here reads the system clock, so the
whole engine is testable without mocking time.

See apps/locker/README_STORAGE_BILLING.md for the state-machine diagram.
"""

import logging
import math
from datetime import datetime, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone as dj_timezone

from apps.locker.models import Batch, UserQuota

logger = logging.getLogger('security')

# "Physically in warehouse" — must match apps/locker/signals.py.
OPEN_BATCH_STATUSES = ['active_free', 'active_chargeable', 'pending']


# ---------------------------------------------------------------------------
# Rate table (spec Section 3)
# ---------------------------------------------------------------------------

def lookup_daily_rate(parcel_count):
    """Flat per-Trunk-ID daily rate for the given live parcel count.
    ₹100/day for 1-20 parcels, +₹50/day for every additional 10."""
    if parcel_count <= 0:
        return Decimal('0.00')
    tier = max(0, math.ceil((parcel_count - 20) / 10))
    return Decimal(100 + 50 * tier).quantize(Decimal('0.01'))


# ---------------------------------------------------------------------------
# Quota helpers
# ---------------------------------------------------------------------------

def _ensure_current_year(quota, today):
    """Lazy Jan-1 reset: if the quota row is stamped with a prior year,
    reset it to a fresh annual pool. Called under the same
    select_for_update() lock as the pass math that follows."""
    if quota.quota_year != today.year:
        quota.passes_remaining = quota.annual_quota
        quota.passes_used = 0
        quota.quota_year = today.year
        quota.save(update_fields=['passes_remaining', 'passes_used', 'quota_year', 'updated_at'])


def _get_or_create_quota_locked(user, today):
    """Fetch (or create) the user's UserQuota row under select_for_update(),
    then apply the lazy annual reset. This is the single entry point every
    quota-touching function goes through — see spec Rules: UserQuota
    counters are the sole source of truth, never recomputed from Batch
    history at read time (compute_free_batches_remaining is the one
    documented exception, and it writes its result back through here)."""
    quota, created = UserQuota.objects.select_for_update().get_or_create(
        user=user, defaults={'quota_year': today.year}
    )
    if not created:
        _ensure_current_year(quota, today)
    return quota


def get_open_batch(locker):
    """The locker's currently open Batch (active_free/active_chargeable/
    pending), or None. A locker has at most one by unique_open_batch_per_locker."""
    return Batch.objects.filter(locker=locker, batch_status__in=OPEN_BATCH_STATUSES).first()


def _in_grace_period(locker, today):
    """payment_grace_until is set to midnight of (start + 7 days) by
    enter_grace_period. Must stay inclusive (>=) to match
    sync_premium_renewals's resolution boundary (`today > grace_date`),
    which only treats grace as over the day *after* this date."""
    if not locker.payment_grace_until:
        return False
    return dj_timezone.localtime(locker.payment_grace_until).date() >= today


# ---------------------------------------------------------------------------
# Batch creation / mid-batch top-up (spec Sections 5-6, 10)
# ---------------------------------------------------------------------------

@transaction.atomic
def create_batch(locker, today):
    """Open a new Batch for this locker's first in-warehouse parcel. Wraps
    the UserQuota decrement and the Batch insert in a single transaction —
    if the Batch INSERT loses a race on unique_open_batch_per_locker and
    raises IntegrityError, the quota decrement rolls back with it. Callers
    (apps/locker/signals.py) catch that IntegrityError and rejoin the
    winning batch via add_parcel_to_batch instead of retrying create_batch."""
    plan_type = locker.plan_type

    if _in_grace_period(locker, today):
        # Section 10: new batches during a payment-failure grace window get
        # temporary Paid-plan terms and a 'pending' status until resolved.
        plan_type_at_creation = 'paid'
        batch_status = 'pending'
        free_storage_end_date = today + timedelta(days=30)
    elif plan_type == 'free':
        quota = _get_or_create_quota_locked(locker.user, today)
        if quota.passes_remaining > 0:
            quota.passes_remaining -= 1
            quota.passes_used += 1
            quota.save(update_fields=['passes_remaining', 'passes_used', 'updated_at'])
            plan_type_at_creation = 'free'
            batch_status = 'active_free'
            free_storage_end_date = today + timedelta(days=20)
        else:
            plan_type_at_creation = 'free'
            batch_status = 'active_chargeable'
            free_storage_end_date = None
    else:
        plan_type_at_creation = 'paid'
        batch_status = 'active_free'
        free_storage_end_date = today + timedelta(days=30)

    batch = Batch.objects.create(
        locker=locker,
        plan_type_at_creation=plan_type_at_creation,
        quota_year=today.year,
        batch_status=batch_status,
        first_parcel_received_date=today,
        free_storage_end_date=free_storage_end_date,
        current_parcel_count=1,
    )
    pass_consumed = plan_type_at_creation == 'free' and batch_status == 'active_free'
    logger.info(
        f"Batch created: locker={locker.locker_id} plan={plan_type_at_creation} "
        f"status={batch_status} pass_consumed={pass_consumed}"
    )
    return batch


def add_parcel_to_batch(batch, today):
    """A parcel arrives while a batch is already open: join it. Never
    touches free_storage_end_date, never consumes a pass — the daily rate
    is derived from current_parcel_count at billing time, not stored here."""
    batch.current_parcel_count += 1
    batch.save(update_fields=['current_parcel_count', 'updated_at'])
    return batch


@transaction.atomic
def remove_parcel_from_batch(batch, today):
    """A parcel leaves the warehouse (shipped/returned/discarded). Decrements
    current_parcel_count and closes the batch via close_batch the moment it
    hits 0 — closure and the 24-hour refund check always happen together."""
    batch.current_parcel_count = max(0, batch.current_parcel_count - 1)
    batch.save(update_fields=['current_parcel_count', 'updated_at'])
    if batch.current_parcel_count == 0:
        close_batch(batch, today)
    return batch


# ---------------------------------------------------------------------------
# Closure & 24-hour refund (spec Sections 8, 14)
# ---------------------------------------------------------------------------

@transaction.atomic
def refund_pass_if_eligible(batch, today):
    """Section 14: if a batch closes within 24 hours of its
    first_parcel_received_date, refund the pass it consumed. Guarded by
    Batch.refund_issued so a second 0-count event on a later, different
    batch never double-refunds. Because Batch only stores dates (not
    timestamps), "within 24 hours" is implemented as "closed on the same
    calendar day it was opened" — the finest granularity this model has."""
    if batch.refund_issued:
        return False
    if batch.plan_type_at_creation != 'free' or batch.free_storage_end_date is None:
        return False  # no pass was consumed at creation (paid batch, or quota was exhausted)
    if today != batch.first_parcel_received_date:
        return False

    quota = _get_or_create_quota_locked(batch.locker.user, today)
    quota.passes_remaining += 1
    quota.passes_used = max(0, quota.passes_used - 1)
    quota.save(update_fields=['passes_remaining', 'passes_used', 'updated_at'])

    batch.refund_issued = True
    batch.save(update_fields=['refund_issued', 'updated_at'])
    logger.info(f"Pass refunded: locker={batch.locker.locker_id} batch={batch.id}")
    return True


@transaction.atomic
def close_batch(batch, today):
    """Section 8: current_parcel_count hit 0. Closes permanently — any
    remaining free days are lost, first_unpaid_charge_date resets to NULL
    (Section 11's abandonment-clock reset)."""
    batch.batch_status = 'closed'
    batch.closed_at = today
    batch.first_unpaid_charge_date = None
    batch.save(update_fields=['batch_status', 'closed_at', 'first_unpaid_charge_date', 'updated_at'])
    refund_pass_if_eligible(batch, today)
    logger.info(f"Batch closed: locker={batch.locker.locker_id} batch={batch.id} refund_issued={batch.refund_issued}")
    return batch


# ---------------------------------------------------------------------------
# Daily billing job (spec Section 7)
# ---------------------------------------------------------------------------

def run_daily_billing(batch, today):
    """Applies one day of billing logic to a single batch. Returns the
    BatchCharge created, or None if no charge was due (still in the free
    period, or already billed today). Safe to call twice on the same batch
    on the same day: the BatchCharge insert is wrapped in its own nested
    savepoint so a unique_batch_charge_per_day violation only rolls back
    the charge attempt, not the surrounding state transition."""
    from apps.payments.models import BatchCharge  # local import: avoids a
    # models.py-level circular import (payments.models already imports
    # apps.locker.models.Batch; this module must not be imported back from
    # apps.locker.models at load time).

    with transaction.atomic():
        batch = Batch.objects.select_for_update().get(pk=batch.pk)

        if batch.current_parcel_count == 0:
            close_batch(batch, today)
            return None

        if batch.batch_status == 'active_free':
            if batch.free_storage_end_date is not None and today >= batch.free_storage_end_date:
                batch.batch_status = 'active_chargeable'
                if batch.first_unpaid_charge_date is None:
                    batch.first_unpaid_charge_date = today
                batch.save(update_fields=['batch_status', 'first_unpaid_charge_date', 'updated_at'])
            else:
                return None

        if batch.batch_status != 'active_chargeable':
            return None

        rate = lookup_daily_rate(batch.current_parcel_count)
        if batch.first_unpaid_charge_date is None:
            batch.first_unpaid_charge_date = today
            batch.save(update_fields=['first_unpaid_charge_date', 'updated_at'])

        final_rate, _discount = batch.locker.apply_storage_discount(rate)
        try:
            with transaction.atomic():
                charge = BatchCharge.objects.create(
                    batch=batch,
                    charge_date=today,
                    parcel_count_snapshot=batch.current_parcel_count,
                    amount=final_rate,
                    amount_standard=rate,
                )
        except IntegrityError:
            logger.info(f"Daily charge skipped (already billed today): batch={batch.id} date={today}")
            return None

        logger.info(
            f"Daily charge: batch={batch.id} amount={rate} "
            f"parcel_count={batch.current_parcel_count} date={today}"
        )
        return charge


def _bill_retroactive(batch, from_date, to_date):
    """Creates one BatchCharge per day in [from_date, to_date] at the
    batch's current rate, skipping any day already billed. Used only by
    resolve_grace_period's failure path (Section 10)."""
    from apps.payments.models import BatchCharge

    rate = lookup_daily_rate(batch.current_parcel_count)
    final_rate, _discount = batch.locker.apply_storage_discount(rate)
    created = []
    d = from_date
    while d <= to_date:
        try:
            with transaction.atomic():
                charge = BatchCharge.objects.create(
                    batch=batch, charge_date=d,
                    parcel_count_snapshot=batch.current_parcel_count, amount=final_rate,
                    amount_standard=rate,
                )
                created.append(charge)
        except IntegrityError:
            pass
        d += timedelta(days=1)
    return created


# ---------------------------------------------------------------------------
# Quota / downgrade / upgrade (spec Section 9)
# ---------------------------------------------------------------------------

def compute_free_batches_remaining(user, year, today):
    """Fix #2: recompute free-plan passes remaining by counting Batch rows
    actually created while the user held Free-plan status
    (plan_type_at_creation == 'free'), not by inferring from the user's
    *current* plan. This is the one place UserQuota is read alongside
    Batch history — see apply_downgrade, its only caller."""
    quota = _get_or_create_quota_locked(user, today)
    used = Batch.objects.filter(
        locker__user=user, quota_year=year, plan_type_at_creation='free'
    ).count()
    return max(0, quota.annual_quota - used)


@transaction.atomic
def apply_downgrade(locker, today):
    """Paid -> Free downgrade. Resets UserQuota to the Fix #2 recomputed
    value. Does NOT touch any existing open Batch — an active batch keeps
    its original free-storage terms; only batches created after this call
    are affected."""
    locker.plan_type = 'free'
    locker.save(update_fields=['plan_type'])

    remaining = compute_free_batches_remaining(locker.user, today.year, today)
    quota = _get_or_create_quota_locked(locker.user, today)
    quota.quota_year = today.year
    quota.passes_remaining = remaining
    quota.passes_used = max(0, quota.annual_quota - remaining)
    quota.save(update_fields=['passes_remaining', 'passes_used', 'quota_year', 'updated_at'])

    logger.info(f"Plan downgraded: locker={locker.locker_id} passes_remaining={remaining}")
    return quota


@transaction.atomic
def apply_upgrade(locker, today, active_batch=None):
    """Free -> Paid upgrade. Unlocks unlimited future passes (locker.plan_type
    = 'paid'); does not restore any already-consumed Free-plan pass. If a
    batch is active, extends it to the 30-day Paid free period — if that
    extended date is still in the future, the batch reverts from
    active_chargeable to active_free (there's no benefit to the extension
    otherwise)."""
    locker.plan_type = 'paid'
    locker.save(update_fields=['plan_type'])

    if active_batch is not None and active_batch.batch_status != 'closed':
        active_batch.free_storage_end_date = active_batch.first_parcel_received_date + timedelta(days=30)
        if today < active_batch.free_storage_end_date:
            active_batch.batch_status = 'active_free'
        active_batch.save(update_fields=['free_storage_end_date', 'batch_status', 'updated_at'])

    logger.info(f"Plan upgraded: locker={locker.locker_id}")
    return active_batch


# ---------------------------------------------------------------------------
# Grace period (spec Section 10)
# ---------------------------------------------------------------------------

@transaction.atomic
def enter_grace_period(locker, today):
    """Paid renewal failed: start the 7-day grace window. Existing active
    batches are unaffected (their terms are untouched); new batches created
    during the window go through create_batch's _in_grace_period branch."""
    grace_date = today + timedelta(days=7)
    locker.payment_grace_until = dj_timezone.make_aware(datetime.combine(grace_date, datetime.min.time()))
    locker.save(update_fields=['payment_grace_until'])
    logger.info(f"Grace period started: locker={locker.locker_id} until={locker.payment_grace_until}")
    return locker


def _resolve_pending_batch_success(batch, today):
    batch.batch_status = (
        'active_free' if batch.free_storage_end_date and today < batch.free_storage_end_date
        else 'active_chargeable'
    )
    if batch.batch_status == 'active_chargeable' and batch.first_unpaid_charge_date is None:
        batch.first_unpaid_charge_date = today
    batch.save(update_fields=['batch_status', 'first_unpaid_charge_date', 'updated_at'])


def _resolve_pending_batch_failure(batch, today):
    if batch.current_parcel_count == 0:
        close_batch(batch, today)
        return

    quota = _get_or_create_quota_locked(batch.locker.user, today)
    if quota.passes_remaining > 0:
        quota.passes_remaining -= 1
        quota.passes_used += 1
        quota.save(update_fields=['passes_remaining', 'passes_used', 'updated_at'])
        batch.free_storage_end_date = batch.first_parcel_received_date + timedelta(days=20)
        batch.batch_status = 'active_free'
    else:
        batch.free_storage_end_date = None
        batch.batch_status = 'active_chargeable'
        if batch.first_unpaid_charge_date is None:
            batch.first_unpaid_charge_date = today

    batch.plan_type_at_creation = 'free'
    batch.save(update_fields=[
        'free_storage_end_date', 'batch_status', 'first_unpaid_charge_date',
        'plan_type_at_creation', 'updated_at',
    ])

    if batch.free_storage_end_date is not None and today > batch.free_storage_end_date:
        _bill_retroactive(batch, batch.free_storage_end_date + timedelta(days=1), today)
    elif batch.free_storage_end_date is None:
        # Quota was exhausted on recalculation: chargeable from the start,
        # so the retroactive gap runs from the batch's actual opening date.
        _bill_retroactive(batch, batch.first_parcel_received_date, today)


@transaction.atomic
def resolve_grace_period(locker, today, payment_succeeded):
    """After the 7-day grace window: either resume normal Paid billing, or
    execute the Paid->Free downgrade and recalculate every still-'pending'
    batch under Free-plan (20-day) terms, per Section 10's Fix #1."""
    pending_batches = list(Batch.objects.filter(locker=locker, batch_status='pending').select_for_update())

    if payment_succeeded:
        locker.payment_grace_until = None
        locker.save(update_fields=['payment_grace_until'])
        for batch in pending_batches:
            _resolve_pending_batch_success(batch, today)
        logger.info(f"Grace period resolved (payment succeeded): locker={locker.locker_id}")
    else:
        apply_downgrade(locker, today)
        locker.payment_grace_until = None
        locker.save(update_fields=['payment_grace_until'])
        for batch in pending_batches:
            _resolve_pending_batch_failure(batch, today)
        logger.info(f"Grace period resolved (payment failed, downgraded): locker={locker.locker_id}")


# ---------------------------------------------------------------------------
# Abandonment clock (spec Section 11)
# ---------------------------------------------------------------------------

def check_abandonment(batch, today):
    """True once a batch has been overdue for >= 60 days, anchored to
    first_unpaid_charge_date (not free_storage_end_date, to account for
    billing/processing lag). Always False for a closed batch, since
    close_batch resets first_unpaid_charge_date to NULL. This function only
    reports the state — locking the Trunk ID / routing to liquidation is a
    future view/admin action, out of scope for this backend-only spec."""
    if batch.first_unpaid_charge_date is None:
        return False
    overdue_days = (today - batch.first_unpaid_charge_date).days
    is_abandoned = overdue_days >= 60
    if is_abandoned:
        logger.warning(
            f"Abandonment threshold reached: locker={batch.locker.locker_id} "
            f"batch={batch.id} overdue_days={overdue_days}"
        )
    return is_abandoned
