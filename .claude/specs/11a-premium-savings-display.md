# Addendum to spec 11: Lifetime Premium Savings Display

Extends `.claude/specs/11-pricing.md` (Phases A–F) — no changes to those phases'
behavior. This adds one more thing to every page that already shows pricing:
a lifetime savings figure.

## What

- **Premium users** see: *"You've saved ₹X with Premium so far"* — real money
  already discounted, summed across every paid/approved TrunkAssist quotation,
  paid shipment, and paid storage batch charge in their history.
- **Free users** see: *"You could have saved ₹X with Premium so far — upgrade
  now"* — a hypothetical, computed by re-applying today's discount rates
  (25% service fee, 5% shipping, 20% storage) to what they actually already
  paid.
- Shown on: dashboard, profile, every TrunkAssist quotation page, every
  shipment detail page. Hidden entirely when the amount is ₹0 (a brand-new
  account with no paid history yet) — no empty/zero banner noise.

## Why a hypothetical for Free users, not "0"

Free users have never had a discount applied — `service_fee_amount ==
service_fee_standard_amount`, `shipping_cost == shipping_cost_standard`,
`BatchCharge.amount == amount_standard` for every one of their records — so
the *actual discount fields* are all zero by construction. The useful number
for a Free user isn't "you saved ₹0" (true but uninformative); it's "here's
what 25%/5%/20% off your actual historical spend would have been," which is
what actually motivates an upgrade decision.

## Implementation history

**v1 (live aggregate queries)** — a shared `calculate_premium_savings(locker)`
function ran three `.aggregate(Sum(...))` queries per page load, one per
source. Flagged as a performance question up front; at the time, the call
was made to skip caching since the aggregates are cheap and FK-scoped to a
single user's small history. Superseded by v2 below on the user's explicit
follow-up request — kept as `apps/accounts/services.py::
calculate_premium_savings()`, no longer called by any view, now serving only
as the audit/backfill source of truth (see Migration below).

**v1 also had a real bug**, fixed before the v2 rewrite: the Free-branch
hypothetical used each record's `actual` (already-paid) amount instead of
`standard` (undiscounted). A currently-Free locker can still have
Premium-priced history from before it downgraded — there `actual < standard`
already, so using `actual` discounted an already-discounted figure a second
time. Fixed to use `standard` throughout; regression-tested in
`apps/accounts/test_premium_savings.py::PremiumSavingsMixedHistoryTests`.

**v2 (current) — denormalized counter, updated incrementally:**

- `Locker.premium_savings_amount` (`apps/accounts/models.py`) — a
  `DecimalField`, the one number every page reads. No live query.
- `Locker.record_premium_savings(standard_amount, rate)` — increments it via
  an atomic `F()`-expression `UPDATE` (`Locker.objects.filter(pk=self.pk)
  .update(premium_savings_amount=F('premium_savings_amount') + increment)`),
  not read-modify-write on `self` — safe under concurrent payments for the
  same locker, and works with a bare `Locker(pk=locker_id)` (no full
  `SELECT` needed), which `_mark_batch_charges_paid` relies on when grouping
  charges across lockers.
- `Locker.premium_savings_display` — a zero-query `@property` that formats
  the stored amount into the same `{'is_premium', 'amount', 'label'}` shape
  the old function returned, so `templates/accounts/
  _premium_savings_banner.html` needed **no changes** — it never knew the
  underlying source changed.
- **Always `standard_amount * rate`, regardless of current `plan_type`.**
  When Premium, that discount was actually applied at the time
  (`standard − actual == standard * rate` by construction of
  `apply_*_discount`), so it's real money saved; when Free, it's the
  hypothetical. Same formula either way — the two v1 branches collapse into
  one, because they were always computing the identical number. Only the
  *label* (`premium_savings_display`) depends on current `plan_type`.

**Three exact finalize-as-paid call sites** — the only places
`premium_savings_amount` changes after the initial backfill:

1. `PersonalShopRequest.mark_paid()` (`apps/personal_shop/models.py`) —
   right after `active_quotation.status` flips to `'approved'`, guarded to
   `quotation_type == 'purchase'` (research_fee/expense_advance have no
   `service_fee_standard_amount` to discount).
2. `_record_shipment_premium_savings(shipment)` (`apps/payments/views.py`)
   — called from both `VerifyPaymentView.post` and `RazorpayWebhookView.post`
   right after `shipment.payment_status` flips to `'paid'` and `shipment.save()`.
   One shared helper, not duplicated across the two call sites — same
   pattern as this file's existing `_mark_batch_charges_paid`/
   `_activate_premium_subscription`.
3. `_mark_batch_charges_paid(payment)` (`apps/payments/views.py`) — this one
   is a bulk `.update()` (bypasses `save()`), so charges are snapshotted
   (`amount_standard`, grouped by `batch__locker_id`) *before* the bulk
   status flip, then each affected locker gets one grouped increment —
   never a per-row loop.

**Migration**: `apps/accounts/migrations/0006_locker_premium_savings_amount.py`
adds the field (default `0.00`); `0007_backfill_premium_savings_amount.py`
(data migration) seeds it for every existing locker from the same formula
above, applied once at deploy time — otherwise the switch to a denormalized
counter would silently zero out savings customers already had.

**Views** (`DashboardView`, `ProfileView`, `PersonalShopQuotationView`,
`ShipmentDetailView`) now read `locker.premium_savings_display` directly —
zero additional queries, down from 3 aggregate queries each.

## Verification

`python manage.py check` passes. Full suite (`apps.accounts`,
`apps.payments`, `apps.personal_shop`, `apps.shipments`) — 319 tests, all
passing, including 23 in `apps/accounts/test_premium_savings.py` covering:
the mixed-history bug fix, `record_premium_savings`'s atomicity/no-op/bare-pk
behavior, `premium_savings_display`'s label wording and zero-hiding, and all
three incremental finalize-as-paid call sites (including the multi-locker
grouped-increment path in `_mark_batch_charges_paid`).
