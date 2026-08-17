# Spec: TrunkAssist Service-Charge Timing & Depth

> **Note (post-implementation, superseded by later decisions):** two things below no longer match what shipped, per explicit follow-up product decisions made during implementation review:
> - **Pricing source**: `apps/personal_shop/pricing.py` was originally written as a pure in-memory dict (no DB access, as described below). It now reads from the admin-editable `ServiceCharge` model (`apps/content/models.py`) instead, so a rate change on the Service Charges admin page takes effect without a deploy — see `pricing.suggested_service_fee()` for the real logic.
> - **`total_amount` formula**: the flat "sum every field" formula described in this doc's Rules section was replaced by a per-`quotation_type` formula — Research Fee and Expense Advance quotations total to that single field alone (`research_fee_amount` / `travel_expense_amount`), not summed with shipping/service-fee/gateway-charge. See `PersonalShopQuotationAdmin.save_related` in `apps/personal_shop/admin.py` for the real formula.
>
> The rest of this document is otherwise accurate; treat the two source files above as authoritative where they disagree with the text below.

## Overview

TrunkAssist (`07-personal-shopper.md`) currently issues exactly one quotation per request, entirely hand-typed by staff in Django admin, and treats every request type identically: quote → pay → purchase. Per `CamelTrunk_Trunk_Assist_Service_Charge_Timing_and_Depth_Guide.pdf` (V1), that's wrong for two of the six request types:

- **Custom Request** must collect a *research fee* before staff do substantial sourcing/comparison work, then issue a **separate** purchase quotation afterward once the item is found.
- **Boutique Purchase (physical visit)** and **Local Shop Purchase** must collect a *service fee + expense advance* before travel, then **settle** the actual travel/local cost afterward (collect balance or refund excess).

Product Link, Image Search and Cart Screenshot stay single-quote-and-pay (no charge if the customer declines before purchase) — they only need correct percentage-based pricing and a documented "this got complicated, escalate it" path.

This spec adds a `quotation_type` to the existing `PersonalShopQuotation` model so a request can carry more than one quotation with different payment/refund semantics, a `work_started_at` timestamp on `PersonalShopRequest` that flips a paid fee non-refundable, a `travel_expense_amount` line on the quotation for physical-visit costs, and a pure pricing helper that prefills (never enforces) the PDF's suggested fee. The existing quotation → Razorpay payment → admin-driven purchase flow from spec 07 is reused unchanged for every stage — this is additive, not a rewrite.

**Out of scope:** GST/tax and legal wording (PDF explicitly defers this to CA/CS/legal review before launch); automated Razorpay refunds for settlement overpayment (staff use the existing `Payment.refund_amount`/`refund_id` fields and Razorpay dashboard manually, same as today); a dedicated staff console (still bare Django admin, per spec 07's already-documented limitation).

## Depends on

- `07-personal-shopper.md` — this spec extends `apps/personal_shop` models/admin/templates, does not replace them
- Existing `apps/payments` Payment model + RazorpayService (unchanged — a research fee or expense advance is just another `Payment` row with `personal_shop_request` set, exactly like a purchase payment today)

## App(s) touched

`apps/personal_shop` only (models, admin, templates, one new pricing module). No new app, no changes outside `apps/personal_shop`.

## Routes

No new routes. The existing quotation/payment routes (`quotation/`, `quotation/decline/`, `quotation/pay/`, `payment/confirmation/`) already operate on "whatever `active_quotation` currently is" — they work unmodified whether that quotation is a research fee, an expense advance, or a purchase, because the request can cycle through multiple quotations already (`PersonalShopQuotation.request` is a plain FK, `related_name='quotations'`; re-quotes already create new rows per spec 07's line 49).

## Model changes

**`apps/personal_shop/models.py` — `PersonalShopQuotation`:**
- `quotation_type = models.CharField(max_length=20, choices=[('purchase', 'Purchase'), ('research_fee', 'Research Fee'), ('expense_advance', 'Expense Advance')], default='purchase')`
- `travel_expense_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)` — separate line from `service_fee_amount` per the PDF's "keep service charge, product cost, travel/transport and shipping as separate line items" rule (§12 checklist). Included in `total_amount` alongside the existing four amount fields in `PersonalShopQuotationAdmin.save_related`.
- `is_refundable` property: `True` unless `quotation_type in ('research_fee', 'expense_advance') and self.request.work_started_at is not None` — mirrors the PDF's §10 table (research/expense fees are refundable right up until staff mark work as started, then not). **This property answers a post-payment question ("can staff refund this now?") and must only be used for that** — e.g. a future refund-eligibility guard in admin. It is never `False` before payment, since `work_started_at` can't be set until `active_quotation.status == 'approved'` (see `mark_work_started` gating below) — so it must not drive the pre-payment warning shown on the pay screen (see Templates).

**`apps/personal_shop/models.py` — `PersonalShopRequest`:**
- `work_started_at = models.DateTimeField(null=True, blank=True)` — stamped by a new admin action (below) once staff actually begin research or travel after a research-fee/expense-advance payment. Presence of this field is what `PersonalShopQuotation.is_refundable` checks; it does not gate anything else in the existing status machine.

Migration: one migration in `apps/personal_shop` adding these three fields.

**New `apps/personal_shop/pricing.py`:**
```python
from decimal import Decimal

SUGGESTED_FEE = {
    'product_link': (Decimal('0.05'), Decimal('199')),
    'image_search': (Decimal('0.06'), Decimal('299')),
    'cart_screenshot': (Decimal('0.06'), Decimal('299')),
    'boutique_purchase': (Decimal('0.07'), Decimal('399')),
}
FLAT_FEE = {
    'local_shop_purchase': Decimal('499'),
}
CUSTOM_REQUEST_STARTING_FEE = Decimal('499')

def suggested_service_fee(request_type, product_value=None):
    """Returns the PDF's suggested fee, or None when staff must set one manually
    (custom_request has no formula — 'final fee based on complexity' per §8)."""
    if request_type in SUGGESTED_FEE:
        rate, minimum = SUGGESTED_FEE[request_type]
        if product_value is None:
            return minimum
        return max(product_value * rate, minimum)
    if request_type in FLAT_FEE:
        return FLAT_FEE[request_type]
    if request_type == 'custom_request':
        return CUSTOM_REQUEST_STARTING_FEE
    return None
```
Pure function, no DB access — a suggestion only. It is never used to validate or reject a staff-entered `service_fee_amount`; per the PDF's §12 checklist ("allow admin to manually quote exceptional custom/travel work"), staff can always override.

## Templates

**Modify:**
- `templates/personal_shop/quotation.html` — render a distinct heading/badge per `quotation.quotation_type` ("Research Fee", "Expense Advance (Travel)", "Purchase Quotation"), show `travel_expense_amount` as its own line when non-zero, and show one fixed sentence above the pay button driven by `quotation_type` alone (**not** `is_refundable` — the quotation is always unpaid at this point, so `is_refundable` is always `True` and would show the wrong message; see Model changes): `purchase` → "Fully refundable if you decline before purchase."; `research_fee`/`expense_advance` → "This fee becomes non-refundable once we begin work." This is the PDF's §12 "show refundable/non-refundable treatment before every payment" requirement — it's a forward-looking warning shown pre-payment, distinct from the post-payment `is_refundable` check a future staff refund guard would use.

No new templates.

## Files to change

- `apps/personal_shop/models.py` — add the three fields above
- `apps/personal_shop/admin.py`:
  - `PersonalShopQuotationAdmin` — add `quotation_type`, `travel_expense_amount` to the inline/edit form; include `travel_expense_amount` in the `save_related` total-amount sum; set the initial `service_fee_amount` on the add form from `pricing.suggested_service_fee(request.request_type)` (a form `initial=` value, not a constraint)
  - `PersonalShopRequestAdmin` — add a `mark_work_started` action ("🚧 Mark Work Started (fee becomes non-refundable)"), enabled only where `active_quotation.quotation_type` is `research_fee`/`expense_advance` and its status is `approved`; sets `work_started_at`
  - `PersonalShopRequestAdmin.list_display` — add `work_started_at` so staff can see refund-eligibility at a glance
- `templates/personal_shop/quotation.html` — quotation-type badge, travel line, refundability sentence (above)
- `apps/personal_shop/migrations/000X_quotation_type_and_settlement_fields.py`

## Files to create

- `apps/personal_shop/pricing.py` (shown above)

## New dependencies

None.

## Rules for implementation

- Every quotation created for a `custom_request` while the request has no prior quotation is a `research_fee` quotation, no product line items yet (per PDF §8: "Scope assessment → Research fee quote → Pay → Research"). The follow-up quotation staff create after research is done — once the item/options are known — is `quotation_type='purchase'` with real line items, same as any other request type's quotation.
- Every quotation created for a `boutique_purchase` request where the note/`type_details` indicates a physical visit is required, or for a `local_shop_purchase` request, is `quotation_type='expense_advance'` first; staff create a second `purchase`-type quotation after the visit to reconcile actual product + actual travel cost against the advance (collect balance via a normal `pay` flow if more is owed, or process a manual refund via `Payment.refund_amount`/Razorpay dashboard if the advance overshot — no new refund code path).
- `boutique_purchase` "basic coordination" (exact boutique + exact item, no travel) and all of `product_link`/`image_search`/`cart_screenshot` stay single `purchase`-type quotations — do not force the two-stage flow where the PDF doesn't call for it (§6 "Basic coordination" row: quote → pay → purchase, same as today).
- Staff escalate an `image_search`/`cart_screenshot` request to `custom_request` by editing `request_type` directly in admin (already a plain editable field, no lock) when the search becomes extensive (§5 depth trigger), or a `boutique_purchase` request when it turns into cross-boutique sourcing (§6 "Complex sourcing" row) — document both cases as a staff runbook note in the admin's `request_type` `help_text`, not new code.
- `mark_work_started` must not be callable before the gating quotation's payment is captured (`active_quotation.status == 'approved'`) — a research fee or advance that hasn't been paid can't be "started."
- `PersonalShopQuotationAdmin.save_related` must include `travel_expense_amount` in the `total_amount` sum alongside the four existing amount fields — a one-line change to the existing `subtotal + obj.domestic_shipping_amount + obj.service_fee_amount + obj.payment_gateway_charge` expression.
- Do not add a scheduled task or signal for any of this — exactly like spec 07's quotation-expiry check, `work_started_at` and `quotation_type` are read lazily wherever they matter (quotation template, refund decision), no cron job.

## Definition of done

- [ ] `python manage.py migrate` runs clean; `PersonalShopQuotation` has `quotation_type` and `travel_expense_amount` columns; `PersonalShopRequest` has `work_started_at`
- [ ] Creating a quotation for a `custom_request` in admin defaults `service_fee_amount` to ₹499 and can be saved as `quotation_type=research_fee` with zero line items
- [ ] Paying that research-fee quotation advances the request through the existing `paid` flow; staff can then run "Mark Work Started" (only once `active_quotation.status == 'approved'`), which stamps `work_started_at`
- [ ] A second quotation created afterward on the same request (`quotation_type=purchase`, real line items) becomes the new `active_quotation` and is payable through the existing pay flow, unaffected by the earlier research-fee quotation's history
- [ ] Creating a quotation for a `local_shop_purchase` request defaults `service_fee_amount` to ₹499 (flat, per `pricing.suggested_service_fee`) and supports `travel_expense_amount` as its own field, included in `total_amount`
- [ ] `/personal-shop/requests/<id>/quotation/` shows the correct type label and, before payment, the correct forward-looking refundability sentence driven by `quotation_type` (a `research_fee`/`expense_advance` quotation warns "non-refundable once work begins" even though it hasn't been paid yet — this must not read `is_refundable`, which is always `True` pre-payment)
- [ ] Declining a `research_fee`/`expense_advance` quotation before `work_started_at` is set behaves exactly like declining today (no payment collected); once `work_started_at` is set, the paid amount is not automatically refunded (manual process, per Rules)
- [ ] Existing spec 07 flows (Product Link, Image Search, Cart Screenshot, Boutique basic coordination) are unaffected — single quotation, `quotation_type='purchase'` by default, no behavior change
