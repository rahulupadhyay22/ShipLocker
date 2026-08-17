# Storage & Billing System — Implementation Summary & Manual Test Guide

Implements `.claude/specs/09-storage-fee.md`. Full technical writeup of the
state machine and design decisions: `apps/locker/README_STORAGE_BILLING.md`.
This doc is a plain-language summary of what changed and how to click
through it yourself.

## What changed, in one paragraph

CamelTrunk used to charge storage per-parcel: every parcel got a flat 30
free days, then a fixed daily fee. That's gone. Storage is now billed
per-Trunk-ID: the first parcel you receive (with no other batch already
open) starts a "batch" with a free period — 20 days if you're on the Free
plan (capped at 3 batches/year), 30 days if you're on Paid. Every other
parcel that arrives while that batch is still open just joins it — free
days don't reset, don't extend. Once the free period lapses, a flat daily
fee applies based on how many parcels are currently sitting in your locker
(₹100/day for 1–20, +₹50/day per extra 10). The batch closes the moment
your locker is empty; any unused free days are lost, and the next parcel
starts a brand-new batch.

## Files touched (grouped by what they do)

| Area | Files |
|---|---|
| New models | `apps/accounts/models.py` (`Locker.plan_type`, `.payment_grace_until`), `apps/locker/models.py` (`Batch`, `UserQuota`), `apps/payments/models.py` (`BatchCharge`, `Payment.payment_type`) |
| Migrations | `apps/accounts/migrations/0004_...`, `apps/locker/migrations/0007_...` + `0008_backfill_...`, `apps/payments/migrations/0004_...` + `0005_delete_storagefee.py` |
| Billing engine | `apps/locker/services/batch_billing.py` (all the actual rules) |
| Wiring | `apps/locker/signals.py`, `apps/locker/apps.py` (Parcel status changes → batch updates, automatic) |
| Daily job | `apps/locker/management/commands/sync_storage_batches.py` — **not yet scheduled anywhere, see warning below** |
| Admin | `apps/locker/admin.py`, `apps/payments/admin.py` (StorageFee admin → BatchCharge admin) |
| Cleanup of the old system | `apps/payments/services.py`, `apps/payments/views.py`, `apps/shipments/admin.py`, `apps/shipments/views.py`, `apps/accounts/views.py` — every place that used to read per-parcel storage fields now reads from the locker's one open batch instead |
| Tests | `apps/locker/tests/test_batch_billing.py` (20 tests, all 13 spec scenarios + concurrency/quota edge cases) |
| Docs | `apps/locker/README_STORAGE_BILLING.md` (technical), this file (manual test guide) |

## ⚠️ Known gap before this can be relied on in production

`sync_storage_batches` is the daily job that actually applies charges. It
is **not wired to any scheduler** — Railway, Render, and the Procfile in
this repo have no cron mechanism configured (not even for the pre-existing
`sync_tracking` command). Until someone sets that up, batches will sit in
`active_free` forever and no one will ever actually get billed. This needs
an owner and a decision on which platform's cron feature to use.

## How to test it manually

You'll need a Django shell (`python manage.py shell`) and/or the admin
panel at `/manage-rb-panel/`. Everything below can be done on your local
dev DB.

### 1. Watch a batch open when a parcel arrives

```python
from apps.accounts.models import User, Locker
from apps.locker.models import Parcel, Batch

user = User.objects.create_user(email='test-storage@example.com')
locker = Locker.objects.create(user=user)  # defaults to plan_type='free'

parcel = Parcel.objects.create(locker=locker, status='pending', item_name='Test Item')

batch = Batch.objects.get(locker=locker)
print(batch.batch_status)          # active_free
print(batch.free_storage_end_date) # 20 days from today
```

### 2. Confirm a second parcel joins the same batch (doesn't reset the clock)

```python
parcel2 = Parcel.objects.create(locker=locker, status='pending', item_name='Second Item')
batch.refresh_from_db()
print(Batch.objects.filter(locker=locker).count())  # still 1
print(batch.current_parcel_count)                    # 2
print(batch.free_storage_end_date)                   # unchanged from step 1
```

### 3. Run the daily billing job

Once a batch's free period has actually passed (you can fast-forward by
editing `batch.free_storage_end_date` directly to yesterday in the shell,
or wait it out in a real scenario):

```python
from django.utils import timezone
batch.free_storage_end_date = timezone.localdate()
batch.save()
```

Then from a terminal:

```bash
python manage.py sync_storage_batches --dry-run   # preview, no writes
python manage.py sync_storage_batches              # actually bills
python manage.py sync_storage_batches              # run again — should create 0 new charges
```

You should see console output like `✓ <locker-id>: charged ₹100.00` the
first time, and `Skipped (still free, or already billed today): 1` the
second time (proves it won't double-charge).

Check it landed:

```python
from apps.payments.models import BatchCharge
BatchCharge.objects.filter(batch=batch).values('charge_date', 'amount', 'status')
```

### 4. Watch a batch close when the locker empties out

```python
parcel.status = 'shipped'
parcel.save()
parcel2.status = 'shipped'
parcel2.save()

batch.refresh_from_db()
print(batch.batch_status)   # closed
print(batch.current_parcel_count)  # 0
```

### 5. Check it in the actual UI, not just the shell

- Log in as `test-storage@example.com` and visit **My Trunk** — the "days
  left" shown on each card should reflect the batch's free period, not a
  hardcoded 30.
- Visit the **Dashboard** — "Storage Left" and "Avg. Days Storage" tiles
  should show real numbers pulled from the batch (not error out).
- Open the parcel's detail page — the "Storage Left" stat should say
  "Overdue" once the batch goes chargeable, or show real days remaining
  otherwise.
- In the admin (`/manage-rb-panel/`), open **Locker & Parcels → Parcels**
  and check the "Storage" column — it now reflects the locker's shared
  batch state, not a per-parcel number.
- In the admin, **Payments → Batch Charges** should list what
  `sync_storage_batches` created in step 3.

### 6. Free-plan quota exhaustion (optional, more involved)

```python
from apps.locker.models import UserQuota
from apps.locker.services import batch_billing as bb
from datetime import date, timedelta

today = date.today()
u2 = User.objects.create_user(email='quota-test@example.com')
l2 = Locker.objects.create(user=u2)  # free plan

for i in range(3):
    b = bb.create_batch(l2, today + timedelta(days=i * 30))
    bb.close_batch(b, today + timedelta(days=i * 30 + 2))  # not same-day, so no refund

quota = UserQuota.objects.get(user=u2)
print(quota.passes_remaining)  # 0

fourth = bb.create_batch(l2, today + timedelta(days=100))
print(fourth.batch_status)            # active_chargeable
print(fourth.free_storage_end_date)   # None — no free days left
```

### 7. Run the automated test suite

```bash
python manage.py test apps.locker.tests.test_batch_billing -v 2
```

All 20 should pass. For a full regression check across the apps this
touched:

```bash
python manage.py test apps.locker apps.payments apps.shipments apps.accounts
```

(148/149 tests pass repo-wide; the one pre-existing failure —
`ApproveDeclarationMethodTests.test_skipped_when_no_shipping_cost_set` — is
unrelated to this work, left over from an earlier shipping-flow change.)
