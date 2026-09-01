# Spec: Shipment Add-on Services + Auto Shipment-Type

## Overview
The Create Shipment wizard's step 3 ("Choose Service") currently only lets the
customer manually pick `shipment_type` (International/Domestic) via radio
buttons — a choice that's actually fully determined by the delivery country
already entered in step 2. This spec:

1. Replaces the manual radio group with a read-only, auto-derived shipment
   type (India → domestic, anything else → international).
2. Adds four opt-in paid add-on services the customer can select in the same
   step: **Insurance**, **Extra Photos**, **Priority Packing**, **Gift
   Wrapping**.
3. Fixes a confirmed pre-existing billing bug, as its own explicitly-called-out
   change (not folded silently into the add-ons work) — see "Confirmed bug:
   consolidation_fee is displayed as owed but never charged" below.

## Confirmed bug: consolidation_fee is displayed as owed but never charged
`CreatePaymentOrderView` (`apps/payments/views.py:170-195`) computes the
actual Razorpay charge as `shipping_due + pending_storage_total` — it has
never included `consolidation_fee`, in any commit since the field was
introduced (`git log -S"consolidation_fee" -- apps/payments/views.py` shows
exactly one touch to that file, unrelated to this line). Meanwhile the
customer-facing "Amount Due" shown before checkout
(`_payment_summary()`'s `shipment_amount_due`) **does** include
`consolidation_fee` — so a Free-plan customer sees a total that includes
consolidation but is only ever actually charged the shipping+storage
portion of it. Premium-plan customers are unaffected (consolidation is
waived to ₹0 for them). This is a real bug, not an intentional split — an
intentional design would not show the customer a due amount it never
collects.

This fix is **in scope for this change** (the line needs to be touched
regardless, to wire in add-ons), but is implemented and committed as its
own explicit fix, separate from the add-ons feature commit, with a commit
message documenting the historical gap. No automatic billing reconciliation
is performed by this change — determining whether any already-paid
Free-plan shipments were underbilled requires querying production data
(this repo's local dev DB is SQLite seed data, not representative), e.g.:
```sql
SELECT display_id, consolidation_fee, shipping_cost, paid_at
FROM shipments_shipment
WHERE payment_status = 'paid' AND consolidation_fee > 0
ORDER BY paid_at DESC;
```
cross-referenced against `payments_payment.amount` actually captured per
shipment. That reconciliation, if needed, is a follow-up outside this
spec's scope.

## Depends on
None of specs 01-08 gate this. Builds on the existing `Shipment` model, the
`ServiceCharge` admin-editable fee table (`apps/content/models.py`), and the
existing consolidation-fee pattern (`apps/payments/services.py`) as the
template for "locked-in fee, admin-editable rate."

## App(s) touched
- `shipments` — new model, view changes, template changes
- `content` — new `ServiceCharge` seed rows + `KNOWN_SERVICE_CHARGE_CODES`
- `payments` — `CreatePaymentOrderView` total-due calculation, `Invoice`
  snapshot field, GST taxable-amount calc

No new app needed.

## Data model

### `apps/shipments/models.py` — new `ShipmentAddon`
```python
class ShipmentAddon(models.Model):
    ADDON_CHOICES = [
        ('insurance', 'Insurance'),
        ('extra_photos', 'Extra Photos'),
        ('priority_packing', 'Priority Packing'),
        ('gift_wrapping', 'Gift Wrapping'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name='addons')
    code = models.CharField(max_length=20, choices=ADDON_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['shipment', 'code']
```
A child table rather than four boolean+amount column pairs on `Shipment` —
smaller schema, and gives per-line invoice rows without extra work. No
`quantity` (none of the four add-ons need one — all are flat per-shipment
picks). No `_standard`/discounted amount pair — add-ons do **not** get the
25% Premium-plan discount that shipping/storage/consolidation get (per
product decision: these are opt-in extras, not baseline service).

`amount` is locked in at shipment creation, same rationale as
`Shipment.consolidation_fee` — an admin changing the `ServiceCharge` rate
later doesn't retroactively change what an existing shipment owes.

### `apps/content/models.py`
Add to `KNOWN_SERVICE_CHARGE_CODES`:
```python
('addon_insurance', 'Add-on: Insurance'),
('addon_extra_photos', 'Add-on: Extra Photos'),
('addon_priority_packing', 'Add-on: Priority Packing'),
('addon_gift_wrapping', 'Add-on: Gift Wrapping'),
```

### `apps/payments/models.py` — `Invoice`
Add `addons_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)`,
snapshotted alongside `consolidation_fee_amount` at invoice generation time.

## Pricing
- **Insurance** (`addon_insurance`): `charge_type='percentage'`, e.g. rate
  2.00%, floor amount ₹99. Computed via the existing
  `ServiceCharge.compute(product_value)` — `max(value * rate/100, floor)` —
  against `sum(parcel.item_price or 0 for parcel in selected_parcels)`.
  `item_price` is staff-set only (confirmed by commit 4d5a7db removing
  customer-facing price entry), so no new UI is needed to collect a value.
- **Extra Photos** (`addon_extra_photos`): flat, e.g. ₹149.
- **Priority Packing** (`addon_priority_packing`): flat, e.g. ₹299.
- **Gift Wrapping** (`addon_gift_wrapping`): flat, e.g. ₹99.

All four seeded via a new `content` migration
(`00XX_seed_addon_service_charges.py`, same `get_or_create`-by-`code`
pattern as `0010_seed_service_charge_codes.py`), and admin-editable
afterward through the existing `ServiceChargeAdmin` — no new admin code
needed since it's generic over `ServiceCharge` rows.

If a `ServiceCharge` row for a given `addon_*` code is missing or
`is_active=False`, that add-on option is **hidden** from the step-3 UI
entirely (unlike the mandatory consolidation fee, these are optional
upsells — no configured price means don't offer it, rather than defaulting
to free).

## Shipment type derivation
`shipment_type` is no longer read from a POST field. In
`CreateShipmentView.post` (`apps/shipments/views.py`), after
`validate_address` produces `address_data['country']`:
```python
shipment_type = 'domestic' if address_data['country'].strip().upper() == 'INDIA' else 'international'
```
This matches the existing `_match_shipping_zone`'s
`country.strip().upper()` convention. The old
`shipment_type = request.POST.get('shipment_type', 'international')` line
and its `if shipment_type not in dict(Shipment.TYPE_CHOICES)` validation are
deleted — there is no longer a client-supplied value to validate.

**This is a literal string comparison, not a `ShippingZone` database
lookup** — it does not query `ShippingZone` at request time at all, so it
cannot break if that table's "India" row is ever renamed, deactivated, or
edited. That row only matters for a *different* concern: whether "India"
appears as a selectable option in the step-2 country `<select>` in the
first place (populated from `ShippingZone.get_countries_list()`) — a
pre-existing UI-availability property of this codebase, unrelated to and
unaffected by this derivation logic. Verified today: there is an active
zone named "India" with `countries="India"`, so "INDIA" is a valid option
value.

No fallback/error path is needed for the derivation itself: any
`address_data['country']` value that isn't literally "India"
(case-insensitive) — including an unrecognized or malformed value from a
direct POST bypassing the dropdown — resolves to `'international'`, which
is a safe, non-crashing default and matches the *old* code's own default
(`request.POST.get('shipment_type', 'international')`). There is no
undefined state to guard against.

`SavedAddress` rows are created from the same `address_data` dict, so
round-tripping a saved address preserves the same casing
(`selectSavedAddress` in create.html writes `data-country` straight into
`form.country.value`).

## Wizard UI (`templates/shipments/create.html`)

**Step 1** (Select Items): each `.parcel-select-card` gets a new
`data-item-price="{{ parcel.item_price|default:'0' }}"` attribute, mirroring
the existing `data-weight`/`data-billable-weight`, so Insurance's live price
can be computed client-side as item selection changes (the wizard is a
single-page client-side stepper with no server round-trip between steps 1
and 3).

The sidebar's *"Your items are 100% insured — We provide full insurance
coverage for your shipment"* box is reworded to avoid contradicting the new
paid Insurance add-on:
> "Basic protection included on every shipment — add Insurance for full
> coverage of your declared value."

**Step 3** (Choose Service):
- Radio group (`name="shipment_type"`) removed.
- New read-only badge showing the derived type — "🌍 International
  Shipment" / "🚚 Domestic Shipment (within India)" — computed in JS from
  `form.country.value`, refreshed whenever country changes (same
  `updateSummary()`/`country` `onchange` hook already wired for the
  shipping estimate).
- New "Add-on Services" card: four checkboxes (`name="addons"`,
  reusing the existing `.radio-card` visual style with `type="checkbox"`
  instead of `type="radio"`), each showing name, one-line description, and
  a live price:
  - Insurance's displayed price recomputes from `sum(data-item-price)` of
    currently-checked step-1 parcels, using rate/floor constants injected
    into JS the same way `CONSOLIDATION_FEE` already is.
  - The other three show their static flat price.
- Options with no active `ServiceCharge` configured are simply not
  rendered (server decides this in `get_context_data`, not JS).

`_create_summary_sidebar.html` and the step-4 review recap: new "Add-ons"
line item (same pattern as the existing `.js-consolidation-fee-row`),
folded into `js-sum-estimated-total`. Its own "100% insured" copy gets the
same rewording as step 1's.

## Server-side: creating the add-ons (`ShipmentCreateView.post`)
Inside the existing `transaction.atomic()` block, after the `Shipment` is
created:
```python
requested_addons = set(request.POST.getlist('addons'))
valid_codes = dict(ShipmentAddon.ADDON_CHOICES).keys()
for code in requested_addons & valid_codes:
    amount = _compute_addon_amount(code, parcels)  # never trust a client-supplied price
    if amount is not None:  # None if the ServiceCharge is missing/inactive
        ShipmentAddon.objects.create(shipment=shipment, code=code, amount=amount)
```
`valid_codes` comes straight from `ShipmentAddon.ADDON_CHOICES` — the
model's own canonical set — so a junk/unknown POST value can never create a
row, independent of pricing. Whether an add-on is actually *offered* is a
second, separate gate: `_compute_addon_amount` returns `None` whenever the
`addon_{code}` `ServiceCharge` is missing or inactive, so a known-but-
unpriced code still creates nothing. Both this creation path and the
step-3 template's checkbox list (`get_addon_options()`, below) read the
same `ServiceCharge` rows through the same lookup — there is no second,
separately-maintained "what's available" list that could drift out of sync
with what's rendered to the customer.

`_compute_addon_amount` looks up the `ServiceCharge` by code
(`addon_{code}`) and calls `.compute(sum_item_price)` for `insurance`,
`.compute()` (no `product_value`) for the three flat ones. Lives in
`apps/payments/services.py` next to `_get_consolidation_fee_amount`, same
module other fee lookups already live in. `get_addon_options()` lives
alongside it and is the single source both `CreateShipmentView.get`'s
template context and `.post`'s creation logic read from.

## Payment wiring
- `_payment_summary()` (`apps/shipments/views.py`): `unpaid_charges` becomes
  `shipping_amount + consolidation_fee + addons_total`, where
  `addons_total = shipment.addons.aggregate(Sum('amount'))['amount__sum'] or 0`.
- `CreatePaymentOrderView.post` (`apps/payments/views.py:157-195`): the
  pre-existing bug — `total_due` only ever summed
  `shipping_due + pending_storage_total`, silently never charging
  `consolidation_fee` even though it's displayed to the customer as owed —
  is fixed here, in the same line add-ons need to be wired into:
  ```python
  extra_due = (shipment.consolidation_fee or 0) + addons_total if shipment.payment_status != 'paid' else Decimal('0.00')
  total_due = (shipping_due + extra_due + pending_storage_total).quantize(Decimal('0.01'))
  ```
  `description_parts` gains `'consolidation'`/`'add-ons'` entries when each
  is > 0, consistent with the existing `'shipping'`/`'storage'` entries.

  **Regression guard**: add one test that builds a single shipment with
  shipping, consolidation_fee, add-ons, and pending storage all non-zero,
  then asserts `_payment_summary(shipment)['shipment_amount_due']` equals
  the `total_due` `CreatePaymentOrderView` actually sends to
  `RazorpayService.create_order`. This is deliberately an equivalence
  check between the two independent calculations, not a check that either
  one's formula is "correct" in isolation — that's what let display and
  charge drift apart the first time.
- Invoice generation (`apps/payments/services.py` GST calc, ~line 217-398):
  `addons_amount` is added into the same `taxable_amount` sum as
  `consolidation_fee_amount`, and stamped onto the new
  `Invoice.addons_amount` field.

## Post-creation visibility
`templates/shipments/detail.html` gets a small "Add-ons" section (list of
purchased add-ons + amounts) and an "Add-ons" line in the Payment Summary
card — a charge with no visible record anywhere after creation would be a
support-ticket generator.

**Staff fulfillment visibility**: Extra Photos, Priority Packing, and Gift
Wrapping all require a warehouse-staff action, not just a charge. Without a
way for staff to see what was purchased, nobody is ever told to fulfill
them. `apps/shipments/admin.py`'s `ShipmentAdmin` gets a new
`ShipmentAddonInline` (read-only `code`/`amount`), following the existing
`ShipmentItemInline`/`ShipmentDocumentInline` pattern, so staff working a
shipment in `/manage-rb-panel/` can see its add-ons at a glance.

## Files to change
- `apps/shipments/models.py` — `ShipmentAddon` model
- `apps/shipments/views.py` — `ShipmentCreateView.get`/`.post`,
  `_payment_summary`
- `apps/shipments/admin.py` — `ShipmentAddonInline` on `ShipmentAdmin`
- `apps/content/models.py` — `KNOWN_SERVICE_CHARGE_CODES`
- `apps/payments/services.py` — `_compute_addon_amount`, invoice
  `taxable_amount` calc
- `apps/payments/views.py` — `CreatePaymentOrderView.post`
- `apps/payments/models.py` — `Invoice.addons_amount`
- `templates/shipments/create.html` — step 1 data attrs + copy, step 3
  badge + add-on cards, JS (`updateSummary`, `updateReviewRecap`, new
  addon-price computation)
- `templates/shipments/_create_summary_sidebar.html` — copy + addons row
- `templates/shipments/detail.html` — add-ons visibility

## Files to create
- `apps/content/migrations/00XX_seed_addon_service_charges.py`
- `apps/shipments/migrations/00XX_shipmentaddon.py`
- `apps/payments/migrations/00XX_invoice_addons_amount.py`
- `apps/shipments/tests/test_shipment_addons.py`

## New dependencies
None.

## Rules for implementation
- Never trust a client-submitted add-on price — always recompute
  server-side from the locked `ServiceCharge` row, same as every other fee
  in this codebase.
- `ShipmentAddon` rows are created inside the same `transaction.atomic()`
  block as the `Shipment`, using the already `select_for_update()`-locked
  `parcels` queryset for the insurance value sum.
- Use CSS variables already in `static/css/main.css` — no hardcoded hex
  values in new template sections.
- Reuse `ServiceCharge.compute()` for all four add-on price computations —
  don't reimplement percentage/floor math.
- Log add-on selection through the `security` logger, consistent with other
  user-triggered state changes on shipment creation.

## Known follow-ups (not in scope here)
- `apps/shipments/tests/test_esign_declaration.py:81` currently POSTs a
  `shipment_type` field directly to the create view; that field becomes
  inert. Needs a check at implementation time that the test's `country`
  value still produces the same expected `shipment_type` under the new
  derivation, and the POST dict's now-dead `shipment_type` key removed.
- Default rates/floors above (Insurance 2%/₹99 floor, Extra Photos ₹149,
  Priority Packing ₹299, Gift Wrapping ₹99) are starting points seeded via
  migration — admin can change them in `/manage-rb-panel/` immediately
  after, no deploy needed.

## Definition of done
- [ ] `python manage.py makemigrations content shipments payments` produces
      exactly the three migrations above; `migrate` applies cleanly
- [ ] Step 3 of Create Shipment shows no shipment-type radio buttons, and
      correctly displays "Domestic" for an India address and
      "International" for any other country, without a page reload
- [ ] All four add-on options with a configured `ServiceCharge` appear as
      checkboxes with live, correct prices; an add-on with no
      `ServiceCharge` configured (or `is_active=False`) does not appear
- [ ] Insurance's displayed price updates live when step-1 item selection
      changes, and matches `sum(item_price) * rate` (or the floor) exactly
- [ ] Submitting the form with add-ons checked creates matching
      `ShipmentAddon` rows with server-recomputed (not client-supplied)
      amounts
- [ ] The consolidation_fee billing-gap fix is its own commit, separate from
      the add-ons feature commit, with a message documenting the historical
      bug
- [ ] `CreatePaymentOrderView` charges `shipping + consolidation_fee +
      addons_total + pending_storage` — verified consolidation_fee is now
      actually collected, not just displayed
- [ ] A single end-to-end test asserts `_payment_summary()`'s displayed
      `shipment_amount_due` figure exactly equals the `total_due` actually
      sent to Razorpay by `CreatePaymentOrderView`, for one shipment with
      shipping + consolidation_fee + add-ons + pending storage all present
      simultaneously (not each component asserted in isolation) — this is
      the regression guard for the exact bug class fixed above: two
      separate calculations (display math vs. charge math) drifting apart
      from each other again in the future
- [ ] `ShipmentAdmin` in `/manage-rb-panel/` shows each shipment's purchased
      add-ons to staff (via `ShipmentAddonInline`)
- [ ] The generated GST invoice's `taxable_amount` includes
      `addons_amount`
- [ ] Shipment detail page shows the add-ons purchased and their cost
- [ ] A different user cannot see or infer another user's add-on selection
      (covered by existing `Shipment` ownership scoping — no new endpoint
      introduced)
