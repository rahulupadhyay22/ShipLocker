# Spec: Shipping Service Selection

## Overview
Once a shipment's billable weight is known (staff has entered parcel weight/dimensions so volumetric weight can be calculated), the customer should be able to choose a shipping service tier — Express, Standard, or Economy — on the shipment detail page. Each tier has its own per-zone, per-weight-slab rate. Choosing a tier recalculates and locks in `shipment.shipping_cost`, which feeds the existing total-due calculation already shown on that page.

## Depends on
None of the existing specs (01-06) gate this; it builds on the existing `Shipment`/`ShipmentItem`/`Parcel` models and the existing `ShippingZone`/`ShippingRate` pricing tables already used by the shipping calculator (`apps/content`).

## App(s) touched
- `shipments` — new field, new POST view, detail template change
- `content` — extend `ShippingRate` with a `service_type` field (reuses existing zone/weight-slab rate table instead of a new model)

No new app needed.

## Routes
- `POST /shipments/<uuid:pk>/service/` — `SelectShippingServiceView` — customer selects a service tier for their shipment; recalculates and saves `shipment.shipping_cost` + `shipment.service_type`, redirects back to `shipments:detail` — logged-in — uses `UserOwnershipMixin` (filter by `request.user`) — 404 if shipment isn't the requester's, consistent with `ObjectOwnershipRequiredMixin` convention for not leaking existence.

Guard conditions inside the view (not new routes, just view logic):
- 400/redirect-with-error if `shipment.total_weight_kg` is not set yet (no billable weight = nothing to price).
- 400/redirect-with-error if `shipment.payment_status == 'paid'` or status is past `pending_payment` (service tier is locked once paid, same spirit as `approve_declaration`/`advance_after_payment` guards already on the model).
- Recalculation looks up the `ShippingZone` matching `shipment.country`, then the `ShippingRate` row matching that zone + `service_type` + `shipment.total_weight_kg` falling in `[min_weight, max_weight)`. No matching rate → redirect back with an error message asking the customer to contact support (don't silently guess a price).

## Model changes
- `apps/content/models.py` `ShippingRate`: add `service_type = models.CharField(max_length=20, choices=SERVICE_TYPE_CHOICES, default='standard', db_index=True)` where `SERVICE_TYPE_CHOICES = [('express', 'Express'), ('standard', 'Standard'), ('economy', 'Economy')]`. Update `Meta.ordering` to `['zone', 'service_type', 'min_weight']`. Existing rows migrate to `service_type='standard'` via the field default — admins add Express/Economy rows for each zone/weight slab afterward through the admin panel.
- `apps/shipments/models.py` `Shipment`: add `service_type = models.CharField(max_length=20, choices=Shipment.SERVICE_TYPE_CHOICES, blank=True)` (reuse the same three choices, defined once on `ShippingRate` and imported, not duplicated) to record the customer's selection. `shipping_cost` is unchanged — it continues to be the single source of truth for the amount due, now populated by this flow instead of only by staff.
- One migration in `content` (new field + ordering change) and one in `shipments` (new field).
- No `AppSettings` config needed — service tier names/pricing live in the existing `ShippingZone`/`ShippingRate` admin-editable tables.

## Templates
- **Modify:** `templates/shipments/detail.html` — add a "Choose Shipping Service" section shown when `shipment.total_weight_kg` is set and `shipment.payment_status != 'paid'` and no `service_type` chosen yet (or allow changing it until paid): a plain POST form with three radio options (Express/Standard/Economy), each showing its calculated price for this shipment's weight/zone (computed in the view's `get_context_data`, not in the template). Submitting re-renders the page with the new `shipment_total_amount`/`shipment_amount_due` already computed by the existing context logic (`shipment.shipping_cost` now populated).
- No new templates needed — this is a section added to the existing detail page, not a new page.
- No new static assets — reuse existing radio/card styles and CSS variables already in `static/css/main.css` for the account/detail pages.

## Files to change
- `apps/content/models.py` — `ShippingRate.service_type` field + choices
- `apps/content/admin.py` — expose `service_type` in the `ShippingRate` admin list/filter (check current admin registration before editing)
- `apps/shipments/models.py` — `Shipment.service_type` field
- `apps/shipments/views.py` — `ShipmentDetailView.get_context_data` computes available service options + prices when `total_weight_kg` is set; add `SelectShippingServiceView`
- `apps/shipments/urls.py` — new route
- `templates/shipments/detail.html` — service selection section

## Files to create
- `apps/content/migrations/00XX_shippingrate_service_type.py` (auto-generated via `makemigrations`)
- `apps/shipments/migrations/00XX_shipment_service_type.py` (auto-generated via `makemigrations`)

## New dependencies
No new dependencies.

## Rules for implementation
- Use Django ORM only, no raw SQL unless there's no ORM equivalent
- Parameterised queries only if raw SQL is unavoidable
- The new POST view must use `UserOwnershipMixin` from `indiabox/mixins.py`, not a hand-rolled `request.user` check
- Log the service selection (and any recalculation failure/no-matching-rate case) through the `security` logger, consistent with other user-triggered state changes on shipments
- Use CSS variables from `static/css/main.css` — never hardcode hex values in the new template section
- All templates extend `templates/base.html` (detail.html already does — no change needed there)
- Don't duplicate the `SERVICE_TYPE_CHOICES` tuple — define it once (on `ShippingRate`, since pricing owns the concept) and import it into `Shipment`
- Reuse `ShippingRate.calculate_price()` for the actual price math — don't reimplement fixed/per-kg pricing logic in the view

## Definition of done
- [x] `python manage.py makemigrations content shipments` produces the two migrations above with no other unexpected changes; `python manage.py migrate` applies cleanly
- [x] In `/manage-rb-panel/`, an admin can add Express/Standard/Economy `ShippingRate` rows for a zone/weight slab
- [x] As a customer with a shipment whose parcels have no weight/dimensions yet, the shipment detail page shows no service-selection section
- [x] After staff set a parcel's weight (or dimensions) so `shipment.total_weight_kg` is populated, the service-selection section appears on that shipment's detail page with three priced options
- [x] Selecting a service tier and submitting recalculates `shipping_cost` correctly (matches `ShippingRate.calculate_price()` for that zone/weight/service combination) and the page's total-due figures update accordingly
- [x] Selecting a tier when no matching `ShippingRate` exists for that zone/service/weight combination shows an error message and does not corrupt `shipping_cost`
- [x] Once `payment_status == 'paid'`, the service-selection section no longer allows changes (read-only display of the chosen tier)
- [x] A different user cannot POST to `/shipments/<uuid:pk>/service/` for a shipment they don't own (404, verified by hitting the URL directly with another account's shipment id)
