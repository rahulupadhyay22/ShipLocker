# Spec: Membership Pricing Plans (Free / Silver / Gold)

## Overview

Introduce a three-tier membership system (Free, Silver, Gold) that grants real, server-enforced entitlements — storage free-days, storage overage rate, shipping discount (capped), personal-shopper fee formula, and consolidation pricing — instead of frontend labels. A new `apps/plans` app owns plan configuration (admin-editable, current-state) and subscription history (append-only, snapshot-safe). All pricing/discount/fee math is centralized in `apps/plans/services.py`; every existing call site that currently hardcodes or ServiceCharge-looks-up these values is repointed at that service.

**Confirmed with the user:** the new Free-tier values (20 days / ₹100/day overage / ₹50/package consolidation) apply to *all* users, including existing accounts, at launch — no grandfathering. This is a real repricing of Free vs. today's live behavior (30 days / ₹50/day / effectively-free consolidation) and should be called out in release notes.

**Explicit scope cuts (agreed via architecture review, not silently dropped):**
- **Payments stay on the existing Razorpay Orders API.** Per §13 of the brief ("integrate with the existing implementation instead of creating a second payment architecture") and because the account's Razorpay Subscriptions/mandate capability is unknown, renewal is **user-initiated** (click "Renew"/"Upgrade", pay via the existing Orders-API checkout flow). There is no silent gateway auto-debit in V1. All lifecycle *states* required by §13/§18 (activation, renewal, upgrade, downgrade, cancellation, expiry, grace, refund) are still fully modeled and enforced — they're just driven by explicit user/admin/webhook actions rather than a recurring-billing engine.
- **Personal Shopper / TrunkAssist has zero existing footprint** (confirmed by repo-wide search). This spec ships the **fee formula only** (`calculate_personal_shopper_fee`, Plan fields for percent/minimum) so it's centrally configured and ready to consume. The actual request/fulfillment workflow (users submitting a purchase-assist request) is a separate future feature — out of scope here.
- **Repacking limits, package-inspection tier, and support tier are display-only labels** stored on `Plan` for the pricing page/comparison table. No repacking workflow, inspection-tier logic, or support-ticket system exists today to gate — building one is out of scope.
- **No plan-config versioning table.** Matching existing precedent (`Invoice` snapshots amounts, `Shipment.consolidation_fee` "locked in at creation," `shipping_cost` locked at service selection), entitlements resolve **live** from the current `Plan` row; only the amount actually charged is snapshotted (`Payment.amount`, `Shipment.shipping_discount_amount`, `StorageFee.fee_amount`, `Subscription.price_paid`).

## Depends on

- `.claude/specs/08-shipping-service-selection.md` (shipping-cost locking flow this spec extends with discounts).
- Existing Razorpay Orders-API integration in `apps/payments` (`RazorpayService`, `Payment`, webhook).

## App(s) touched

New: `apps/plans`.
Modified: `apps/accounts`, `apps/locker`, `apps/payments`, `apps/shipments`, `apps/notifications`, `apps/content` (read-only reference, no model changes), `indiabox` (urls/settings).

## Routes

- `GET /plans/` — `PricingPageView` — public pricing page (cards, comparison table, "which plan is right for me" quiz) — no auth — no ownership mixin (public).
- `POST /plans/recommend/` — `PlanRecommendationView` — computes a plan recommendation from quiz answers using the real entitlement formulas, returns `JsonResponse` — no auth — no ownership mixin (public, stateless, no user data).
- `GET /account/plan/` — `ManagePlanView` — current plan, entitlements, usage, savings, upgrade/downgrade/cancel entry points — `LoginRequiredMixin`.
- `GET /account/plan/change/<str:plan_code>/<str:cycle>/` — `ChangePlanConfirmView` — price preview + confirmation before payment — `LoginRequiredMixin`.
- `POST /account/plan/change/<str:plan_code>/<str:cycle>/order/` — `CreateSubscriptionOrderView` — creates `Payment(payment_type='plan_subscription')` + Razorpay order, price read from `Plan` row server-side only — `LoginRequiredMixin`, `SecureActionMixin`.
- `POST /account/plan/cancel/` — `CancelSubscriptionView` — sets `cancel_at_period_end=True` on the active `Subscription`; logs `SubscriptionEvent` — `LoginRequiredMixin`, `LockerOwnershipMixin`, `SecureActionMixin`.
- `POST /account/plan/downgrade-now/` — `DowngradeNowView` — immediate downgrade to Free (bypasses period-end wait) — `LoginRequiredMixin`, `LockerOwnershipMixin`, `SecureActionMixin`.
- `GET /account/billing/` — `BillingHistoryView` — list of subscription `Payment`/`Invoice` rows for the user — `LoginRequiredMixin`, `LockerOwnershipMixin`.

Reused, not new (extended in place):
- `POST /payments/verify/` (`VerifyPaymentView`) — branches on `Payment.payment_type` to call `activate_subscription()` for `plan_subscription` payments alongside the existing shipment-paid path.
- `POST /payments/webhook/razorpay/` (`RazorpayWebhookView`) — adds `WebhookEvent` dedup and the same `payment_type` branch; fixes the existing unconditional overwrite in the `payment.failed` handler (must check current status before mutating, matching the guard already used for `payment.captured`).

## Model changes

**New app `apps/plans`, `apps/plans/models.py`:**

```python
class Plan(models.Model):
    CODE_CHOICES = [('free', 'Free'), ('silver', 'Silver'), ('gold', 'Gold')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=10, choices=CODE_CHOICES, unique=True)
    name = models.CharField(max_length=50)
    tagline = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)

    monthly_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    annual_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    storage_free_days = models.PositiveIntegerField()
    storage_overage_daily_rate = models.DecimalField(max_digits=10, decimal_places=2)

    shipping_discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    shipping_discount_max_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    personal_shopper_percent = models.DecimalField(max_digits=5, decimal_places=2)
    personal_shopper_minimum_fee = models.DecimalField(max_digits=10, decimal_places=2)

    consolidation_fee_per_package = models.DecimalField(max_digits=10, decimal_places=2)
    consolidation_included_packages = models.PositiveIntegerField(default=0)

    free_package_photos = models.PositiveIntegerField(default=0)
    inspection_tier = models.CharField(max_length=20, default='standard')   # display-only
    repacking_tier = models.CharField(max_length=20, default='paid')       # display-only
    processing_tier = models.CharField(max_length=20, default='standard')  # display-only
    support_tier = models.CharField(max_length=20, default='standard')     # display-only

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class Subscription(models.Model):
    STATUS_CHOICES = [('active','Active'), ('grace','Grace Period'), ('cancelled','Cancelled'), ('expired','Expired')]
    BILLING_CYCLE_CHOICES = [('monthly','Monthly'), ('annual','Annual'), ('none','N/A - Free')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    locker = models.ForeignKey('accounts.Locker', on_delete=models.CASCADE, related_name='subscriptions')
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name='subscriptions')
    billing_cycle = models.CharField(max_length=10, choices=BILLING_CYCLE_CHOICES, default='none')
    price_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # snapshot of amount charged
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active', db_index=True)
    started_at = models.DateTimeField(auto_now_add=True)
    current_period_end = models.DateTimeField(null=True, blank=True)  # null = Free, never expires
    cancel_at_period_end = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['locker'], condition=models.Q(status__in=['active', 'grace']),
                name='unique_active_subscription_per_locker',
            )
        ]

class SubscriptionEvent(models.Model):
    EVENT_CHOICES = [
        ('created','Created'), ('activated','Activated'), ('renewed','Renewed'),
        ('upgraded','Upgraded'), ('downgraded','Downgraded'), ('cancel_scheduled','Cancel Scheduled'),
        ('cancelled','Cancelled'), ('expired','Expired'), ('grace_started','Grace Started'),
        ('payment_failed','Payment Failed'), ('refunded','Refunded'),
    ]
    SOURCE_CHOICES = [('user','User'), ('admin','Admin'), ('webhook','Webhook'), ('system','System')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name='events')
    event_type = models.CharField(max_length=20, choices=EVENT_CHOICES)
    old_plan = models.ForeignKey(Plan, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    new_plan = models.ForeignKey(Plan, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES)
    reference_id = models.CharField(max_length=100, blank=True)  # payment.display_id or admin user id
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

**`apps/payments/models.py` changes:**
- `Payment`: add `payment_type = models.CharField(max_length=20, choices=[('shipment','Shipment'),('plan_subscription','Plan Subscription')], default='shipment')` and `subscription = models.ForeignKey('plans.Subscription', on_delete=models.SET_NULL, null=True, blank=True, related_name='payments')`.
- `Invoice`: change `shipment = models.OneToOneField(Shipment, on_delete=models.PROTECT, related_name='invoice')` to `null=True, blank=True`; add `subscription = models.ForeignKey('plans.Subscription', on_delete=models.PROTECT, null=True, blank=True, related_name='invoices')`; add a `CheckConstraint` (or `clean()`) requiring exactly one of `shipment`/`subscription` set. `invoice_number` generation (`generate_invoice_number`) is reused unchanged.
- New `WebhookEvent` model: `event_id` (unique, from Razorpay's `X-Razorpay-Event-Id` or payload `event.id`), `event_type`, `payload` (JSONField), `processed_at` (nullable), `created_at`. Checked/inserted before any webhook side effect.

**`apps/shipments/models.py` — `Shipment`:**
- Add `shipping_discount_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)` — locked in at service-tier selection, same pattern as `shipping_cost`.
- Add `plan_code_at_discount = models.CharField(max_length=10, blank=True)` — audit/display only, records which plan produced the discount.

**`apps/notifications/models.py` — `AppSettings`:**
- Add `max_shipping_discount_percent_safety_ceiling = models.DecimalField(max_digits=5, decimal_places=2, default=50.00)` — the one global guard-rail referenced in admin validation below.
- Add WhatsApp template name fields, mirroring the existing `template_parcel_added` pattern: `template_plan_activated`, `template_plan_payment_failed`, `template_plan_cancelled`, `template_plan_expiring_soon`.

**Migrations:**
- `apps/plans/migrations/0001_initial.py` — creates `Plan`, `Subscription`, `SubscriptionEvent`.
- `apps/plans/migrations/0002_seed_plans_and_backfill_subscriptions.py` — **data migration**: creates the three `Plan` rows with the values in the table below, then creates one `Subscription(plan=free, status='active', billing_cycle='none', current_period_end=None)` for every existing `Locker` that doesn't already have one. Non-destructive; wrapped so it can be re-run safely (`get_or_create`).
- `apps/payments/migrations/000X_payment_type_and_subscription_fk.py`
- `apps/payments/migrations/000X_invoice_nullable_shipment.py`
- `apps/payments/migrations/000X_webhookevent.py`
- `apps/shipments/migrations/000X_shipment_discount_fields.py`
- `apps/notifications/migrations/000X_appsettings_plan_fields.py`

**Seeded plan values (Free applies to all users per the confirmed decision above):**

| Field | Free | Silver | Gold |
|---|---|---|---|
| monthly_price | 0 | 249 | 449 |
| annual_price | 0 | 2499 | 4499 |
| storage_free_days | 20 | 30 | 45 |
| storage_overage_daily_rate | 100 | 75 | 50 |
| shipping_discount_percent | 0 | 8 | 15 |
| shipping_discount_max_amount | — | 500 | 1000 |
| personal_shopper_percent / minimum_fee | 7 / 249 | 4 / 199 | 2 / 149 |
| consolidation_fee_per_package | 50 | 25 | 25 |
| consolidation_included_packages | 0 | 0 | 6 |

## Templates

Create:
- `templates/plans/pricing.html` — cards, monthly/annual toggle, comparison table, quiz.
- `templates/plans/manage_plan.html` — current plan, entitlements, savings, action buttons.
- `templates/plans/change_plan_confirm.html`
- `templates/plans/billing_history.html`

Modify:
- `templates/accounts/profile.html` — add plan summary section (current plan, renewal date, savings, storage/discount/consolidation usage, manage-plan links).
- `templates/base.html` — nav link to `/plans/`.
- Shipment service-selection template (from spec 08) — show discount preview line before commit.

Static assets: `static/js/plans.js` (toggle + quiz, no new round-trip except the one `/plans/recommend/` call). Pricing-card styling added to `static/css/main.css` using existing CSS variables — no new hardcoded palette.

## Files to change

- `apps/locker/models.py` — `Parcel.days_remaining_free`/`storage_days`: replace hardcoded `30` with a plan-aware lookup via `apps.plans.services.get_plan_entitlements(parcel.locker)`; compute free-until/overdue using `Asia/Kolkata` **calendar dates** (`localtime(received_at).date()` + `timedelta(days=free_days)` vs. `localtime(now()).date()`), not raw UTC `timedelta.days`, to avoid off-by-one at day boundaries.
- `apps/accounts/models.py` — add a `Locker.active_subscription` helper property (`self.subscriptions.filter(status__in=['active','grace']).first()`).
- `apps/accounts/views.py` — `DashboardView.get_context_data`: replace hardcoded `'trunk_capacity': 30` with the plan's `storage_free_days`. `ProfileView.get`: add plan/entitlement/savings context.
- `apps/payments/models.py` — model changes above.
- `apps/payments/views.py` — `VerifyPaymentView`: branch on `payment.payment_type`, call `apps.plans.services.activate_subscription(payment)` for `plan_subscription`. `RazorpayWebhookView`: add `WebhookEvent` dedup before processing; fix `payment.failed` handler to check `payment.status not in ('captured',)` before overwriting, matching the existing `payment.captured` guard; add the same `payment_type` branch.
- `apps/payments/services.py` — `_get_daily_storage_fee_amount()` / `_get_consolidation_fee_amount()`: replace `ServiceCharge` name-matching with `apps.plans.services.calculate_storage_fee` / `calculate_consolidation_fee`, parametrized by the parcel's/shipment's locker. `ensure_storage_fee_for_parcel()`: update the hardcoded `30` the same way as `Parcel.days_remaining_free` (still not wired to any signal/cron — this spec does not add automation that doesn't already exist). `CreatePaymentOrderView`'s `total_due` computation: include `shipment.consolidation_fee` (currently missing from the Razorpay order total — existing gap, fixed here) and use `shipment.shipping_cost - (shipment.shipping_discount_amount or 0)` instead of raw `shipping_cost`.
- `apps/payments/admin.py` — register `WebhookEvent` (read-only, mirrors `InvoiceAdmin`'s no-add/no-delete pattern).
- `apps/shipments/models.py` — model changes above.
- `apps/shipments/views.py` — `SelectShippingServiceView.post`: after `rate.calculate_price(weight)`, call `apps.plans.services.calculate_shipping_discount(request.user, price)`, store both `shipping_cost` (pre-discount) and `shipping_discount_amount`; `plan_code_at_discount` from the resolved plan. `CreateShipmentView.post`: replace `_get_consolidation_fee_amount()` call with `apps.plans.services.calculate_consolidation_fee(request.user, len(parcels))`. `_payment_summary()`: include discount in the displayed/aggregated totals.
- `apps/notifications/models.py` — `AppSettings` field additions above.
- `apps/notifications/services.py` — `WhatsAppService.get_template_name`: extend the event-name→template-field map with the four new `plan_*` events.
- `indiabox/settings.py` — add `'apps.plans'` to `INSTALLED_APPS`.
- `indiabox/urls.py` — `path('plans/', include('apps.plans.urls'))`.
- `apps/payments/tests.py`, `apps/shipments/`, `apps/locker/` — no destructive changes; existing tests must keep passing (`Invoice.shipment` becoming nullable is backward compatible; `ensure_storage_fee_for_parcel`'s existing tests, if any, should be checked against the new date-math).

## Files to create

- `apps/plans/__init__.py`, `apps/plans/apps.py`, `apps/plans/models.py`, `apps/plans/admin.py`, `apps/plans/services.py`, `apps/plans/views.py`, `apps/plans/urls.py`, `apps/plans/signals.py`, `apps/plans/tests.py`
- `apps/plans/migrations/0001_initial.py`, `apps/plans/migrations/0002_seed_plans_and_backfill_subscriptions.py`
- `apps/payments/migrations/000X_payment_type_and_subscription_fk.py`, `000X_invoice_nullable_shipment.py`, `000X_webhookevent.py`
- `apps/shipments/migrations/000X_shipment_discount_fields.py`
- `apps/notifications/migrations/000X_appsettings_plan_fields.py`
- `templates/plans/pricing.html`, `templates/plans/manage_plan.html`, `templates/plans/change_plan_confirm.html`, `templates/plans/billing_history.html`
- `static/js/plans.js`

## New dependencies

"No new dependencies." — no Razorpay SDK, no Django REST Framework, no Celery/cron package. Recommendation notifications (`template_plan_expiring_soon`) run via a new `python manage.py notify_expiring_subscriptions` management command, invoked by the same external scheduler mechanism already used for `sync_tracking` — no new scheduling infrastructure introduced.

## Rules for implementation

- Use Django ORM only. No raw SQL unless absolutely necessary; parameterised queries only if raw SQL is unavoidable.
- Ownership mixins for authenticated user-owned resources: `LockerOwnershipMixin` for anything querying by `Subscription.locker`/`Payment.subscription`; `ObjectOwnershipRequiredMixin` where a single object is fetched by pk.
- Security logging through the `security` logger for: subscription created/upgraded/downgraded/cancelled, webhook signature failures, webhook dedup rejections — mirror the existing patterns in `VerifyPaymentView`/`RazorpayWebhookView`.
- CSS variables from `static/css/main.css` only.
- Templates extend `templates/base.html`.
- **All pricing/discount/fee calculations happen exclusively in `apps/plans/services.py`.** No view, template, or JS ever computes a chargeable amount — `CreateSubscriptionOrderView` re-reads the `Plan` row server-side from `plan_code` in the URL and never accepts a price from the client; `SelectShippingServiceView`/`CreateShipmentView` similarly never trust a client-supplied discount or fee.
- Webhook idempotency: check `WebhookEvent` for an existing `event_id` before any mutation; insert it inside the same transaction as the mutation it guards.
- Storage-day and free-until-date math must use `Asia/Kolkata` calendar dates (`django.utils.timezone.localtime(...).date()`), not raw UTC `timedelta.days`, per `TIME_ZONE='Asia/Kolkata'` / `USE_TZ=True` in `indiabox/settings.py`.
- No gateway auto-debit in V1 — renewal is user-initiated through the existing Orders-API checkout flow; do not add a Subscriptions/Plans API integration or a cron-based auto-charge job.
- Admin validation (`PlanAdminForm.clean()`): hard `ValidationError` for negative prices/days/fees, `shipping_discount_percent`/`personal_shopper_percent` outside `[0, 100]`, `shipping_discount_percent` above `AppSettings.max_shipping_discount_percent_safety_ceiling`, and non-zero `monthly_price`/`annual_price` on the `free` plan code. Soft `messages.warning()` in `PlanAdmin.save_model()` (not a hard block) for hierarchy inversions — e.g. Gold's `shipping_discount_percent` lower than Silver's, or Gold's `storage_overage_daily_rate` higher than Silver's — mirroring the only existing form-validation precedent in `apps/accounts/forms.py::AddressForm.clean()`.
- Audit: every `Subscription` lifecycle transition writes a `SubscriptionEvent` row (timestamp/subscription/event_type/old_plan/new_plan/source/reference_id are the required fields — this satisfies the brief's audit-logging list without a second logging system). Every `Plan` admin edit writes an `AdminLog` row via `PlanAdmin.save_model()` (first real writer of that existing-but-currently-unused model) — do not build a third audit mechanism.
- Entitlements resolve live from the current `Plan` row via `Locker.active_subscription`; only actually-charged amounts are snapshotted (see Overview). Do not add plan-config versioning.
- No Django REST Framework. Use server-rendered template context; the sole exception is `POST /plans/recommend/`, a plain `JsonResponse` view, because the quiz genuinely needs a round-trip to the real formulas — do not introduce DRF for it or anything else in this feature.

## Definition of done

- [ ] `GET /plans/` (logged out) shows three pricing cards with a working monthly/annual toggle and the seeded prices; Gold is visually marked as best value.
- [ ] Comparison table values match the seeded `Plan` rows exactly.
- [ ] "Which plan is right for me" quiz returns a recommendation computed from `apps/plans/services.py` formulas (verify with two different answer sets, not a hardcoded lookup table).
- [ ] A newly created `Locker` automatically has an active Free `Subscription` (no manual step).
- [ ] Data migration gives every pre-existing `Locker` an active Free `Subscription` — verify via admin list count matches `Locker.objects.count()`.
- [ ] Shipping discount matches all six brief examples: Free ₹5,000→₹0; Silver ₹5,000×8%=₹400; Silver ₹10,000×8%=₹800→capped ₹500; Gold ₹5,000×15%=₹750; Gold ₹10,000×15%=₹1,500→capped ₹1,000.
- [ ] Personal shopper fee matches all six brief examples (Free/Silver/Gold × ₹2,000/₹10,000 item value) via `calculate_personal_shopper_fee` (unit test or Django shell check — no request-intake UI exists yet, per scope cut).
- [ ] Storage fee matches all six brief examples: Free 20d→₹0, 21d→₹100; Silver 30d→₹0, 31d→₹75; Gold 45d→₹0, 46d→₹50 — verified with parcels whose `received_at` is set to exact day boundaries in IST.
- [ ] Consolidation fee matches all five brief examples: Free 3pkg→₹150; Silver 3pkg→₹75; Gold 3pkg→₹0; Gold 6pkg→₹0; Gold 8pkg→₹50.
- [ ] Free→Silver upgrade: `CreateSubscriptionOrderView` creates a Razorpay order for exactly ₹249 (monthly) or ₹2,499 (annual); on `VerifyPaymentView` success, a new active `Subscription` exists, the prior one is no longer `active`/`grace`, and a `SubscriptionEvent(event_type='upgraded')` is logged.
- [ ] Gold→Silver downgrade takes effect at `current_period_end` by default (banner on `manage_plan.html` shows the scheduled change); `DowngradeNowView` applies it immediately when explicitly requested.
- [ ] Cancelling a paid plan sets `cancel_at_period_end=True`; entitlements remain at the paid tier until `current_period_end`; after that timestamp, the next entitlement read demotes to Free with no cron involved (verify by backdating `current_period_end` in admin and reloading the account page).
- [ ] No scheduled task silently re-charges a user — renewal always requires an explicit checkout action.
- [ ] Posting the same Razorpay webhook payload twice does not double-activate a subscription, double-generate an invoice, or double-log a `SubscriptionEvent` (`WebhookEvent` dedup).
- [ ] A replayed/duplicate `payment.failed` webhook does not overwrite an already-`captured` `Payment`.
- [ ] Account page (`/account/plan/` and the profile page) shows current plan, renewal date, billing amount, savings this month, total membership savings, storage usage (X/Y days), shipping discount %, personal-shopper %/minimum, and consolidation (X/Y included) — all derived from real `Subscription`/`Plan`/transaction data.
- [ ] Admin cannot save a `Plan` with a negative price, negative storage days, a discount percent outside 0–100, a discount percent above the configured safety ceiling, or a non-zero price on the `free` plan code — each raises a form validation error.
- [ ] Admin sees a non-blocking warning when saving a plan hierarchy inversion (e.g., Gold discount % lower than Silver's).
- [ ] Setting a `Plan.is_active=False` removes it from `/plans/` without affecting existing `Subscription`s already on that plan.
- [ ] A `plan_subscription` payment generates a GST invoice via the existing `InvoiceService` (subscription-linked, no shipment) with a valid sequential invoice number.
- [ ] A shipment's GST invoice computes tax on the post-discount shipping amount, and `CreatePaymentOrderView`'s Razorpay order total now includes `consolidation_fee` (previously missing).
- [ ] `python manage.py test apps.plans apps.payments apps.shipments apps.locker` passes, including all pre-existing tests (no regressions from the `Invoice.shipment` nullability change or the storage-day date-math rewrite).
