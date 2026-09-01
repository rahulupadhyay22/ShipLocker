# Spec: Shipment Add-on Services + Auto Shipment-Type

## Overview
The Create Shipment wizard's step 3 ("Choose Service") no longer lets the
customer manually pick `shipment_type` (International/Domestic) — it's
derived automatically from the delivery country entered in step 2 (India →
domestic, anything else → international). The same step now also offers
four opt-in paid add-on services: **Insurance**, **Extra Photos**,
**Priority Packing**, **Gift Wrapping**, priced via the existing
admin-editable `ServiceCharge` table. Additionally, a pre-existing billing
bug is fixed: `CreatePaymentOrderView` previously never actually charged a
shipment's `consolidation_fee`, even though it was displayed to customers
as owed.

## Depends on
None of specs 01-12 gate this. Builds on the existing `Shipment` model, the
`ServiceCharge` admin-editable fee table (`apps/content/models.py`), and the
existing `consolidation_fee` pattern (`apps/payments/services.py`).

## App(s) touched
- `shipments` — new model (`ShipmentAddon`), view changes, template changes
- `content` — new `ServiceCharge` seed rows + `KNOWN_SERVICE_CHARGE_CODES`
- `payments` — `CreatePaymentOrderView` total-due calculation, `Invoice`
  snapshot field, GST taxable-amount calc

No new app.

## Data model

### `apps/shipments/models.py` — `ShipmentAddon`
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
`amount` is locked in at shipment creation (never recomputed later, even if
the admin changes the `ServiceCharge` rate afterward). No Premium-plan
discount applies to add-ons — they are opt-in extras, not baseline service.

### `apps/content/models.py`
`KNOWN_SERVICE_CHARGE_CODES` includes four new codes:
`addon_insurance`, `addon_extra_photos`, `addon_priority_packing`,
`addon_gift_wrapping`.

### `apps/payments/models.py` — `Invoice`
`addons_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)`,
snapshotted at invoice generation time alongside `consolidation_fee_amount`.

## Pricing
Four `ServiceCharge` rows (seeded via `apps/content/migrations/
0013_seed_addon_service_charges.py`, admin-editable afterward in
`/manage-rb-panel/` → Service Charges — name/description/amount/
percentage_rate/active state all editable with no deploy needed):
- **Insurance** (`addon_insurance`) — percentage, 2% of declared value
  (`sum(parcel.item_price)` for the shipment's parcels), floor ₹99.
  Computed via `ServiceCharge.compute(product_value)`.
- **Extra Photos** (`addon_extra_photos`) — flat ₹149.
- **Priority Packing** (`addon_priority_packing`) — flat ₹299.
- **Gift Wrapping** (`addon_gift_wrapping`) — flat ₹99.

If an add-on's `ServiceCharge` row is missing or `is_active=False`, that
add-on is **not offered** — it does not appear as a checkbox, and
`_compute_addon_amount()` returns `None` for it (never silently defaults to
free, unlike the mandatory `consolidation_fee`).

Label/description shown to the customer come directly from the
`ServiceCharge` row's own `name`/`description` fields (not a separate
hardcoded copy) — an admin edit to either field takes effect in the wizard
immediately, no deploy.

## Shipment type derivation
`shipment_type` is derived server-side in `CreateShipmentView.post`
(`apps/shipments/views.py`), never trusted from a POST field:
```python
shipment_type = 'domestic' if address_data['country'].strip().upper() == 'INDIA' else 'international'
```
A stray/tampered `shipment_type` POST value has no effect — the derivation
always wins.

## Wizard UI (`templates/shipments/create.html`)
- Step 1 (Select Items): each parcel card carries the item's declared value
  (`data-item-price`) so Insurance's live price can recompute as item
  selection changes.
- Step 3 (Choose Service): the old International/Domestic radio buttons are
  gone. A read-only badge shows the derived type ("🌍 International
  Shipment" / "🚚 Domestic Shipment (within India)"), computed client-side
  from the step-2 country field. Four add-on checkboxes (`name="addons"`)
  render only for add-ons with an active `ServiceCharge`, each showing a
  live price — Insurance recomputes from currently-checked step-1 items;
  the other three show a static flat price.
- The summary sidebar and step-4 review both show an "Add-ons" line item
  and roll it into the estimated/final total.

## Server-side: creating add-ons (`CreateShipmentView.post`)
Inside the existing `transaction.atomic()` block, after the `Shipment` is
created (using the same `select_for_update()`-locked `parcels` queryset
already used for shipment creation):
```python
requested_addons = set(request.POST.getlist('addons'))
valid_codes = dict(ShipmentAddon.ADDON_CHOICES).keys()
for code in requested_addons & valid_codes:
    amount = _compute_addon_amount(code, parcels)  # server-computed, never trusts POST
    if amount is not None:  # None if unconfigured/inactive
        ShipmentAddon.objects.create(shipment=shipment, code=code, amount=amount)
```
- A POST code not in `ShipmentAddon.ADDON_CHOICES` is silently ignored —
  no row created, no error.
- A POST code that IS a known choice but has no active `ServiceCharge` is
  also silently ignored — no row created.
- There is no client-supplied amount field for add-ons at all — the amount
  is always recomputed server-side from the locked `ServiceCharge` row.

## Payment wiring
- `_payment_summary()` (`apps/shipments/views.py`) — the customer-facing
  displayed total — includes `addons_amount` (sum of the shipment's
  `ShipmentAddon.amount`) in `unpaid_charges`, alongside
  `shipping_amount`/`consolidation_fee`. Once `payment_status == 'paid'`,
  the entire `unpaid_charges` figure (including add-ons) is zeroed in
  `shipment_amount_due`.
- `CreatePaymentOrderView` (`apps/payments/views.py`) — the actual Razorpay
  charge — includes the same `addons_total` in `total_due`, gated the same
  way as `shipping_due`/`consolidation_fee_due`: **zero once the shipment is
  already `payment_status == 'paid'`**, so a later order (e.g. triggered by
  a new pending storage charge) never re-charges an already-paid add-on.
  `Payment.description` and `Payment.notes`/the Razorpay order's notes
  correctly attribute the consolidation portion vs. the add-ons portion
  separately (`notes['consolidation_due']` vs. `notes['addons_due']`) — an
  add-on charge is never mislabeled as "consolidation" (e.g. for a Premium
  shipment where `consolidation_fee` is waived to 0 but an add-on was
  purchased).
- **The displayed total and the actually-charged total must always be the
  same number** for any given shipment state — this is the property a
  dedicated regression test in `apps/payments/tests.py` verifies end-to-end
  (calling `_payment_summary()` and POSTing to the real
  `payments:create_order` endpoint for the same shipment, with shipping +
  consolidation + add-ons + pending storage all non-zero simultaneously,
  and asserting the two numbers match).
- GST invoice generation (`InvoiceService.generate_for_shipment`) includes
  `addons_amount` in `taxable_amount`, snapshotted onto `Invoice.addons_amount`.

## Consolidation-fee billing-gap fix (independent of add-ons)
`CreatePaymentOrderView` previously computed `total_due` as
`shipping_due + pending_storage_total` only — `consolidation_fee` was never
included in the actual Razorpay charge, even though `_payment_summary()`'s
displayed `shipment_amount_due` already included it. This is fixed:
`consolidation_fee` (when unpaid) is now included in `total_due`, and
`Payment.description`/`notes` correctly label it as `'consolidation'`
separately from any add-ons.

## Staff visibility
`ShipmentAdmin` (`apps/shipments/admin.py`) has a read-only
`ShipmentAddonInline` showing each shipment's purchased add-ons
(`code`/`amount`/`created_at`) so warehouse staff can see what needs
fulfilling (e.g. gift-wrap the box, take extra photos, jump the packing
queue).

## Customer visibility
`templates/shipments/detail.html`:
- Payment Summary card shows an "Add-ons" line listing each purchased
  add-on and its amount (hidden entirely if the shipment has none).
- The "Insurance" stat in the Shipment Information card reflects whether
  the customer actually purchased the Insurance add-on (`has_insurance_addon`
  in `ShipmentDetailView.get_context_data`) — no longer an unconditional
  "100% Insured" claim.

## Files changed
- `apps/shipments/models.py` — `ShipmentAddon`
- `apps/shipments/views.py` — `CreateShipmentView.get`/`.post`,
  `_payment_summary`, `ShipmentDetailView.get_context_data`
- `apps/shipments/admin.py` — `ShipmentAddonInline`
- `apps/content/models.py` — `KNOWN_SERVICE_CHARGE_CODES`
- `apps/payments/services.py` — `_compute_addon_amount`, `get_addon_options`,
  invoice `taxable_amount` calc
- `apps/payments/views.py` — `CreatePaymentOrderView.post`
- `apps/payments/models.py` — `Invoice.addons_amount`
- `templates/shipments/create.html`, `_create_summary_sidebar.html`,
  `detail.html`

## Migrations
- `apps/content/migrations/0013_seed_addon_service_charges.py`
- `apps/shipments/migrations/0010_shipmentaddon.py`
- `apps/payments/migrations/0011_invoice_addons_amount.py`

## Definition of done
- [x] Step 3 shows no shipment-type radio buttons; correctly displays
      Domestic for an India address, International otherwise
- [x] All add-ons with a configured `ServiceCharge` appear as checkboxes
      with live, correct prices; an unconfigured/inactive add-on does not
      appear
- [x] Insurance's price updates live as step-1 item selection changes
- [x] Submitting with add-ons checked creates matching `ShipmentAddon` rows
      with server-recomputed (never client-supplied) amounts
- [x] An unknown or unconfigured add-on code in the POST creates no row
- [x] `CreatePaymentOrderView` charges shipping + consolidation_fee +
      add-ons + pending storage while unpaid; zero of all three once paid
- [x] Displayed total (`_payment_summary`) and actually-charged total
      (`CreatePaymentOrderView`) are provably the same number for a
      shipment with every fee type present
- [x] `Payment.description`/`notes` never mislabel an add-on charge as
      consolidation, or vice versa
- [x] GST invoice's `taxable_amount` includes `addons_amount`
- [x] `ShipmentAdmin` shows each shipment's purchased add-ons to staff
- [x] Shipment detail page shows purchased add-ons and reflects actual
      Insurance purchase state
- [x] A different user cannot see or influence another user's shipment/
      add-on selection (existing `Shipment` ownership scoping — no new
      endpoint)
