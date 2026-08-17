# Spec: Shipment-Batch Storage & Billing System

## Overview

Replace CamelTrunk's current per-parcel flat storage-fee model (`Parcel.days_remaining_free` hardcoded to 30 days, `StorageFee` per-parcel rows, `_get_daily_storage_fee_amount`, `add_storage_fees` admin action) with a **per-Trunk-ID shipment-batch** model: a batch opens on the first parcel received while no batch is active, grants a free-storage period drawn from a plan-based pass pool, and accrues a flat per-day charge (based on live parcel count, not per-parcel multiplication) once that period lapses. Batches close permanently when parcel count hits zero; unused free days are lost.

This spec adds a **minimal `plan_type` flag** (`free` | `paid`) directly on `Locker`, plus the grace-period flag Section 10 of the brief needs — it does **not** depend on the deleted `09-pricing-plans.md` draft (Free/Silver/Gold tiers, per-plan daily rates). If that tiered-plan system is built later, `paid` maps onto whichever tier a subscription resolves to, and this spec's parcel-count rate table (Section 3 of the brief) is the single source of truth for storage rates — a future plans app must not add a second, competing rate source.

The existing per-parcel `StorageFee` model, `_get_daily_storage_fee_amount()`, `add_storage_fees` admin action, and `_sync_overdue_storage_fees()` are **removed and replaced** by this batch system — running both simultaneously would double-charge users for the same parcels. `Payment` gains a `payment_type` field so batch charges can be paid outside the shipment-checkout flow, mirroring the pattern the deleted plans draft already established for subscription payments.

This is a backend-only deliverable per the brief: data models, a pure/testable billing engine, a daily job entry point, and a full test suite. No new routes, views, or templates.

## Depends on

Nothing — this spec does not require `apps/plans` or any other in-flight spec. It reads/writes only `apps/accounts.Locker`, `apps/locker.Parcel`, and adds new models to `apps/locker` and `apps/payments`.

## App(s) touched

`apps/locker` (batch lifecycle, quota — driven by parcel arrival/departure, which already lives here), `apps/payments` (billing ledger rows, `Payment` extension, deprecation of old storage-fee writers), `apps/accounts` (`Locker.plan_type`, grace-period flag), `apps/shipments/admin.py` (remove the now-dead `add_storage_fees` action and its `_get_daily_storage_fee_amount` import).

## Routes

No new routes. No new templates. This spec is data model + service layer only; a future spec wires the billing engine into checkout/admin UI.

## Model changes

**`apps/accounts/models.py` — `Locker`:**
- `plan_type = models.CharField(max_length=10, choices=[('free', 'Free'), ('paid', 'Paid')], default='free')`
- `payment_grace_until = models.DateTimeField(null=True, blank=True)` — set when a paid-plan renewal fails; cleared on successful renewal or on downgrade completion. Presence of a non-null, non-expired value means "in grace period" (Section 10 of the brief); no separate boolean needed.

**`apps/locker/models.py` — new `Batch` model:**
```python
class Batch(models.Model):
    PLAN_CHOICES = [('free', 'Free'), ('paid', 'Paid')]
    STATUS_CHOICES = [
        ('active_free', 'Active - Free'),
        ('active_chargeable', 'Active - Chargeable'),
        ('closed', 'Closed'),
        ('pending', 'Pending (Grace Period)'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    locker = models.ForeignKey('accounts.Locker', on_delete=models.CASCADE, related_name='batches')

    plan_type_at_creation = models.CharField(max_length=10, choices=PLAN_CHOICES)
    quota_year = models.PositiveIntegerField(help_text="Calendar year of first_parcel_received_date")
    batch_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active_free', db_index=True)

    first_parcel_received_date = models.DateField()
    free_storage_end_date = models.DateField(null=True, blank=True)
    closed_at = models.DateField(null=True, blank=True)
    current_parcel_count = models.PositiveIntegerField(default=0)
    first_unpaid_charge_date = models.DateField(null=True, blank=True)
    refund_issued = models.BooleanField(default=False, help_text="24-hour cancellation pass refund already applied — guards double-refund")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['locker', 'batch_status'], name='idx_batch_locker_status'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['locker'], condition=models.Q(batch_status__in=['active_free', 'active_chargeable', 'pending']),
                name='unique_open_batch_per_locker',
            )
        ]
```
The `unique_open_batch_per_locker` constraint is what actually enforces "one active batch per Trunk ID" — the state machine in the service module must never try to create a second open batch, but the DB is the backstop against a race between two parcel-intake requests.

**`apps/locker/models.py` — new `UserQuota` model:**
```python
class UserQuota(models.Model):
    user = models.OneToOneField('accounts.User', on_delete=models.CASCADE, primary_key=True, related_name='quota')
    annual_quota = models.PositiveIntegerField(default=3)
    passes_used = models.PositiveIntegerField(default=0)
    passes_remaining = models.PositiveIntegerField(default=3)
    quota_year = models.PositiveIntegerField()
    updated_at = models.DateTimeField(auto_now=True)
```
Scoped by `user_id`, not `locker_id`, per the brief's invariant — even though `Locker` is currently a strict `OneToOneField` to `User` (verified in `apps/accounts/models.py`), so today this is equivalent to per-locker but must not be written as a locker FK, since a future multi-locker-per-user change would silently break the shared-pool guarantee otherwise.

**Single source of truth: `UserQuota` counters win, always.** `passes_used`/`passes_remaining` are the live, authoritative balance — every pass consumption or refund is a `select_for_update()`-guarded read-modify-write on this row (same locking pattern as `generate_parcel_id`'s `select_for_update()` in `apps/locker/models.py`), inside the same transaction as the `Batch` row it's paired with. Nothing ever recomputes `passes_remaining` by scanning `Batch` history at read time.

The one exception is `compute_free_batches_remaining` (Section 9 downgrade recalculation), which **does** scan `Batch` history — but it runs exactly once, at the moment of a downgrade event, specifically to produce a corrected value that is then **written into** `UserQuota.passes_remaining`/`passes_used` (a reset, not a parallel read path). After that write, `UserQuota` is authoritative again until the next downgrade. `apply_downgrade` is the only caller of `compute_free_batches_remaining`; no other code path recomputes quota from batches.

**Annual reset is lazy, not a scheduled job.** Every function that reads or consumes `UserQuota` (`create_batch`, `compute_free_batches_remaining`, `refund_pass_if_eligible`) first checks `quota.quota_year != today.year` and, if so, resets `passes_remaining = annual_quota`, `passes_used = 0`, `quota_year = today.year` before doing anything else — under the same `select_for_update()` lock. This makes the Jan 1 reset a side effect of the first quota touch in the new year rather than a `sync_storage_batches`-style cron job that could plausibly not run on Jan 1 itself (holiday, deploy freeze) and leave stale counters. A helper `_ensure_current_year(quota, today)` in `batch_billing.py` centralizes this check so it isn't duplicated at each call site.

**`apps/payments/models.py` — new `BatchCharge` model** (replaces `StorageFee`'s role):
```python
class BatchCharge(models.Model):
    STATUS_CHOICES = [('pending', 'Pending'), ('paid', 'Paid'), ('waived', 'Waived')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey('locker.Batch', on_delete=models.PROTECT, related_name='charges')
    payment = models.ForeignKey(Payment, on_delete=models.SET_NULL, null=True, blank=True, related_name='batch_charges')

    charge_date = models.DateField()
    parcel_count_snapshot = models.PositiveIntegerField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='INR')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    waived_reason = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['batch', 'charge_date'], name='unique_batch_charge_per_day'),
        ]
```
The `unique_batch_charge_per_day` constraint is the idempotency guard for the daily job — running it twice on the same day for the same batch raises `IntegrityError` on the second attempt rather than double-charging; the job entry point catches and skips that specific error per batch.

`batch` uses `on_delete=models.PROTECT`, not `CASCADE` — `BatchCharge` is a financial ledger row; a `Batch` (parent record) being deleted must never silently delete the charges billed against it. In practice `Batch` rows are never deleted by application code (batches only ever transition to `closed`), so `PROTECT` costs nothing functionally and exists purely as a guard against an accidental admin/manual deletion wiping billing history. Same reasoning applies to `Payment.batch_charges` already using `SET_NULL` rather than `CASCADE` — a charge must outlive the payment record that settled it.

**`apps/payments/models.py` — `Payment` changes:**
- Add `payment_type = models.CharField(max_length=20, choices=[('shipment', 'Shipment'), ('storage_batch', 'Storage Batch')], default='shipment')`. `shipment` stays nullable as today; a `storage_batch` payment has `shipment=None` and its `BatchCharge` rows point back via `BatchCharge.payment`.

**Removed:**
- `apps/payments/models.py::StorageFee` — delete the model and its migration-generated table (new migration drops it). `days_overdue`/per-parcel flat-fee concept is superseded by `BatchCharge`.
- `apps/payments/services.py::_get_daily_storage_fee_amount`, `ensure_storage_fee_for_parcel` (if present) — superseded by `apps/locker/services/batch_billing.py::lookup_daily_rate`.
- `apps/locker/views.py::_sync_overdue_storage_fees` — superseded by the daily job (`sync_storage_batches` management command).
- `apps/shipments/admin.py::add_storage_fees` action and its `_get_daily_storage_fee_amount` import; `Parcel.days_remaining_free` / `storage_days` / `is_storage_overdue` properties on `apps/locker/models.py::Parcel` — these encode the old flat-30-day rule and are no longer meaningful once storage is batch-scoped, not parcel-scoped.
- `apps/payments/views.py::_get_pending_storage_fees_for_shipment`, `_mark_storage_fees_paid` — replaced by an equivalent pair operating on `BatchCharge` (kept as private helpers in the same file, same call sites in `VerifyPaymentView`/`RazorpayWebhookView`, extended to also mark a standalone `storage_batch` payment's charges paid).

**Migrations:**
- `apps/accounts/migrations/000X_locker_plan_type_grace.py`
- `apps/locker/migrations/000X_batch_and_userquota.py`
- `apps/locker/migrations/000X_backfill_batches_for_existing_parcels.py` — **data migration, required, not optional.** Without it, every parcel already in the warehouse the day this ships has no `Batch`, so `current_parcel_count` never decrements on its eventual `shipped`/`returned`/`discarded` transition and it silently stops being billable. For each `Locker` with at least one `Parcel.status in {pending, action_required, approved}`: create one `Batch` with `batch_status='active_chargeable'`, `free_storage_end_date=None`, `first_parcel_received_date=` the earliest `received_at` among that locker's open parcels, `current_parcel_count=` the count of those parcels, `plan_type_at_creation=locker.plan_type`, `quota_year=` year of `first_parcel_received_date`. No pass is consumed — these parcels predate the pass system and backdating a pass grant would either falsely shrink a user's 2026 quota or falsely grant free days for storage time that already elapsed under the old flat-30-day rule. Going straight to `active_chargeable` is the conservative default (matches "assume the free period, if any, already lapsed" rather than risk under-charging); ops can waive individual `BatchCharge` rows via the `waived`/`waived_reason` fields if a specific case warrants it.
- `apps/payments/migrations/000X_batchcharge_and_payment_type.py`
- `apps/payments/migrations/000X_remove_storagefee.py`

## Templates

No new templates. No template modifications — UI wiring is out of scope per the brief ("Do not build a UI").

## Files to change

- `apps/accounts/models.py` — add `Locker.plan_type`, `Locker.payment_grace_until`.
- `apps/locker/models.py` — add `Batch`, `UserQuota`; remove `Parcel.storage_days`/`days_remaining_free`/`is_storage_overdue` properties.
- `apps/locker/views.py` — remove `_sync_overdue_storage_fees` and its call sites.
- `apps/locker/signals.py` — **new file**: `post_save` receiver on `Parcel` that calls `batch_billing.on_parcel_received`/`on_parcel_departed` based on status transitions (see Rules below), following the `pre_save`-snapshot-then-`post_save`-diff pattern already used in `apps/shipments/signals.py`.
- `apps/locker/apps.py` — register the new signals module in `ready()`, mirroring `apps/shipments/apps.py`.
- `apps/payments/models.py` — add `BatchCharge`, `Payment.payment_type`; remove `StorageFee`.
- `apps/payments/views.py` — replace `_get_pending_storage_fees_for_shipment`/`_mark_storage_fees_paid` with `BatchCharge`-based equivalents.
- `apps/payments/services.py` — remove `_get_daily_storage_fee_amount`; the rate table now lives only in `apps/locker/services/batch_billing.py`.
- `apps/payments/admin.py` — swap `StorageFee` admin registration for `BatchCharge`.
- `apps/shipments/admin.py` — remove `add_storage_fees` action and the now-dead import.

## Files to create

- `apps/locker/services/__init__.py`
- `apps/locker/services/batch_billing.py` — the core pure/testable service module implementing brief Sections 5–14: `create_batch`, `add_parcel_to_batch`, `close_batch`, `lookup_daily_rate(parcel_count)`, `run_daily_billing(batch, today)`, `compute_free_batches_remaining(user, year)`, `apply_downgrade`, `apply_upgrade`, `enter_grace_period`, `resolve_grace_period`, `check_abandonment(batch, today)`, `refund_pass_if_eligible(batch, today)`. Every function takes `today` as an explicit `date` parameter — no `timezone.now()`/`date.today()` calls inside the module itself, so the whole engine is testable without mocking the clock.
- `apps/locker/management/commands/sync_storage_batches.py` — daily job entry point, mirrors `apps/shipments/management/commands/sync_tracking.py`'s structure: iterates `Batch.objects.filter(batch_status__in=['active_free', 'active_chargeable'])`, calls `batch_billing.run_daily_billing(batch, today=timezone.localdate())` per batch inside a per-batch `try/except` so one bad batch doesn't abort the run, logs through the `security` logger for every charge created (money-relevant, matches existing convention of security-logging payment actions).
- `apps/locker/tests/test_batch_billing.py` — full test suite covering brief Section 15 (all 13 named cases), run via `python manage.py test apps.locker` (no pytest in this repo).
- `apps/locker/README_STORAGE_BILLING.md` — state-machine documentation (`active_free → active_chargeable → closed`, plus `pending` during grace periods) with a diagram.

## New dependencies

"No new dependencies."

## Rules for implementation

- Use Django ORM only. No raw SQL unless absolutely necessary; parameterised queries only if raw SQL is unavoidable.
- Ownership mixins for authenticated user-owned resources: none apply here directly (no new routes), but any future view over `Batch`/`UserQuota`/`BatchCharge` must use `LockerOwnershipMixin`/`ObjectOwnershipRequiredMixin` per `indiabox/mixins.py` convention.
- Security logging through the `security` logger for: batch creation, batch closure, every charge created by the daily job, pass refunds, grace-period entry/resolution, abandonment-lock triggers — mirror the logging already present in `apps/payments/views.py`.
- All storage-day and free-until-date math uses **calendar dates** (`django.utils.timezone.localdate()` / `Asia/Kolkata`), never raw UTC `datetime` deltas — `Batch.first_parcel_received_date`, `free_storage_end_date`, `closed_at`, `first_unpaid_charge_date` are all `DateField`, not `DateTimeField`, specifically to avoid time-of-day drift across the free-period boundary (matches `TIME_ZONE='Asia/Kolkata'` / `USE_TZ=True` in `indiabox/settings.py`).
- **"Physically in warehouse"** (what drives `current_parcel_count`) is `Parcel.status in {pending, action_required, approved}`. A parcel entering `shipped`, `returned`, or `discarded` decrements the batch's `current_parcel_count`; a parcel entering `pending` (first receipt) increments it and triggers batch creation/join logic. This mapping must be encoded once, in `apps/locker/signals.py`, not duplicated at each status-transition call site.
- `apps/locker/signals.py` uses the same `pre_save`-snapshot-then-`post_save`-diff pattern as `apps/shipments/signals.py::store_original_tracking_number` / `sync_tracking_on_number_change` — detect the actual status transition, not just "status is now X", so a parcel saved twice with the same status doesn't double-count. **A brand-new `Parcel` (`created=True`) has no "before" state to diff against** — the `pre_save` snapshot lookup (`Parcel.objects.get(pk=...)`) would raise `DoesNotExist` and there's nothing to compare. Treat `created=True and instance.status == 'pending'` as an explicit, separate transition — "no batch → receiving" — handled as its own branch in the `post_save` receiver, not inferred from a diff. `created=True` with any other initial status is not expected (`Parcel.status` defaults to `'pending'`) and is a no-op for batch purposes.
- **Race backstop:** two concurrent parcel-intake requests for the same locker can both observe "no open batch" and both call `create_batch`; the second `INSERT` hits `unique_open_batch_per_locker` and raises `IntegrityError`. The signal receiver must catch that specific `IntegrityError`, and — inside a fresh transaction — re-fetch the now-existing open `Batch` for that locker and call `add_parcel_to_batch` on it instead, rather than letting the exception propagate (which would 500 the parcel-intake request that triggered it, e.g. warehouse staff marking a parcel received in the admin). This re-join path is exercised by a dedicated concurrency test in `test_batch_billing.py` (two `create_batch` calls for the same locker in the same transaction-committed sequence, second one must join not fail).
- **`create_batch` is wrapped in a single `@transaction.atomic()` block covering both the `UserQuota` decrement and the `Batch` insert.** This is what makes the race backstop above safe: if the `Batch` insert fails on `unique_open_batch_per_locker`, the `UserQuota.passes_remaining` decrement earlier in the same function call rolls back with it — a losing `create_batch` attempt must never leave a pass permanently consumed with no `Batch` row to show for it. The signal receiver's catch-and-rejoin logic runs *after* this atomic block has already rolled back, as a separate, fresh transaction that only calls `add_parcel_to_batch` (no quota touch).
- The rate table (`lookup_daily_rate`) is a hardcoded pure function, not an `AppSettings`-driven config — deliberate deviation from the usual "AppSettings owns admin-editable config" pattern, because the brief specifies exact test amounts (₹100/150/200.../day) that a runtime-editable table would make impossible to pin down as a genuine regression test.
- The daily job (`sync_storage_batches`) must be safe to run twice on the same day: `BatchCharge`'s `unique_batch_charge_per_day` constraint is the enforcement; `run_daily_billing` catches `IntegrityError` per batch and treats it as "already billed today," not a failure.
- No gateway auto-debit — a `storage_batch` `Payment`/Razorpay order is created by a future view/admin action, out of scope here; this spec only guarantees `BatchCharge` rows exist in a payable (`pending`) state with a stable identity for that future flow to collect against, exactly as `StorageFee` did before it.
- Downgrade/upgrade/grace-period transitions (`apply_downgrade`, `apply_upgrade`, `enter_grace_period`, `resolve_grace_period`) are plain service functions called from wherever plan changes originate — this spec does not build the subscription-change UI/webhook that calls them, matching the "no new routes" scope; a future plans spec is expected to call these functions rather than reimplementing Section 9/10 logic.
- CSS variables from `static/css/main.css` only — not applicable, no templates in this spec.
- Templates extend `templates/base.html` — not applicable, no templates in this spec.

## Definition of done

- [ ] `python manage.py makemigrations apps.accounts apps.locker apps.payments` produces the migrations listed above with no unexpected changes; `python manage.py migrate` applies cleanly on a fresh SQLite DB.
- [ ] On a DB seeded with pre-existing `Parcel` rows in `pending`/`action_required`/`approved` status before this migrates, the backfill data migration gives every such locker exactly one `active_chargeable` `Batch` with the correct `current_parcel_count` and no pass consumed — verify `UserQuota.passes_remaining` for those users is unchanged by the backfill.
- [ ] Two `create_batch` calls issued for the same locker without an existing batch (simulating a race) result in exactly one `Batch` row — the second call joins the first via the `IntegrityError` catch-and-rejoin path, it does not raise past the signal receiver.
- [ ] A `UserQuota` row with `quota_year` from the prior year is reset to `passes_remaining=annual_quota`, `passes_used=0`, `quota_year=` current year the first time it's touched after Jan 1 — verified without a scheduled job (call a quota-touching function with `today` set to Jan 2 of a new year and check the row).
- [ ] `StorageFee` model, `_get_daily_storage_fee_amount`, `add_storage_fees` admin action, and `_sync_overdue_storage_fees` no longer exist anywhere in the codebase (`grep -r` returns nothing outside this spec/migration history).
- [ ] Creating a `Parcel` with `status='pending'` when the locker has no open `Batch` creates one, consuming a pass if `plan_type='free'` and `passes_remaining > 0`, or landing in `active_chargeable` with no free period if exhausted, or granting 30 free days unconditionally if `plan_type='paid'` — verified via `python manage.py shell` for one case of each.
- [ ] A second `Parcel` arriving while a `Batch` is open joins the same batch (`current_parcel_count` increments, `free_storage_end_date` untouched, no second `Batch` row created) — verified by inspecting `locker.batches.count()` stays 1.
- [ ] `python manage.py test apps.locker` passes, including all 13 named scenarios from brief Section 15, using only Django's built-in `TestCase` (no pytest, per CLAUDE.md — no test framework is configured in this repo).
- [ ] Running `python manage.py sync_storage_batches` twice in the same day creates no duplicate `BatchCharge` rows (verify `BatchCharge.objects.filter(batch=b, charge_date=today).count() == 1` after both runs).
- [ ] `compute_free_batches_remaining` on a user with 1 free-plan batch in Jan + 6 paid-plan batches Mar–Aug returns `2`, not `0`, when called in Sep after a downgrade (Fix #2 regression test, also covered by the automated suite but worth a manual shell check).
- [ ] A batch's `first_unpaid_charge_date` resets to `NULL` the moment `current_parcel_count` reaches 0, and `check_abandonment` returns `False` for that (now closed) batch regardless of prior overdue balance.
- [ ] `README_STORAGE_BILLING.md` accurately documents the four-state machine and grace-period `pending` state, with a diagram, and matches the actual enum values in `Batch.STATUS_CHOICES`.
