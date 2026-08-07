# Spec: Free / Premium Pricing Plans

## Overview

Add a two-tier plan (Free, Premium) to every Locker. Premium is a one-time, fixed-duration purchase via Razorpay (reusing the existing `Payment`/`RazorpayService` flow — no recurring billing exists in this codebase and none is introduced here). Premium unlocks two perks, both driven by admin-editable `AppSettings` fields rather than hardcoded constants:

1. **Longer free storage window** before `StorageFee` charges apply (replaces the currently hardcoded `30` in `apps/locker/models.py` and `apps/payments/services.py`).
2. **Percentage discount on shipping rates** at service-tier selection time (`apps/shipments/views.py`).

Plan expires after `AppSettings.premium_plan_duration_days`; on expiry the locker silently reverts to Free behavior (`is_premium` becomes computed/false — no cron job needed for MVP, checked lazily wherever perks are read).

## Depends on

None. Builds on existing `Payment`/`RazorpayService` (`apps/payments`), `AppSettings` (`apps/notifications`), `Locker` (`apps/accounts`), `StorageFee`/`Parcel` free-days logic, and `ShippingRate.calculate_price()` (`apps/content`).

## App(s) touched

`apps/accounts`, `apps/notifications`, `apps/payments`, `apps/locker`, `apps/shipments`

## Routes

- `GET /account/plan/` — `PlanView` (apps/accounts) — plan status + comparison table + upgrade CTA — authenticated — `LoginRequiredMixin` (own locker only, read via `request.user.locker`)
- `POST /account/plan/upgrade/order/` — `CreatePlanOrderView` (apps/payments) — creates Razorpay order for `AppSettings.premium_plan_price`, creates `Payment(payment_type='plan_upgrade', shipment=None)` — authenticated — `LoginRequiredMixin`, mirrors `CreatePaymentOrderView`'s duplicate-order guard (recent pending payment within 30 min)

No new verify route: the existing `POST /payments/verify/` (`VerifyPaymentView`) and `RazorpayWebhookView` are extended to branch on `payment.payment_type` and call a shared `_activate_premium_plan(payment)` helper on `payment.captured` — this avoids duplicating signature verification and idempotency handling for a second endpoint.

## Model changes

**`apps/accounts/models.py` — `Locker`**
- `plan` — `CharField(max_length=10, choices=[('free','Free'),('premium','Premium')], default='free')`
- `plan_expires_at` — `DateTimeField(null=True, blank=True)`
- `is_premium` property — `self.plan == 'premium' and (self.plan_expires_at is None or self.plan_expires_at > timezone.now())`

**`apps/notifications/models.py` — `AppSettings`**
- `premium_plan_price` — `DecimalField(max_digits=10, decimal_places=2, default=999.00)`
- `premium_plan_duration_days` — `PositiveIntegerField(default=365)`
- `free_plan_storage_days` — `PositiveIntegerField(default=30)` (replaces the hardcoded `30`)
- `premium_plan_storage_days` — `PositiveIntegerField(default=60)`
- `premium_shipping_discount_percent` — `DecimalField(max_digits=5, decimal_places=2, default=10.00)`

**`apps/payments/models.py` — `Payment`**
- `payment_type` — `CharField(max_length=20, choices=[('shipment','Shipment'),('plan_upgrade','Plan Upgrade')], default='shipment')` — lets `VerifyPaymentView`/`RazorpayWebhookView` dispatch correctly instead of inferring intent from `shipment IS NULL`

**`apps/locker/models.py` — `Parcel`**
- `days_remaining_free` / storage-overdue logic reads the free-days figure from `AppSettings` (`premium_plan_storage_days` if `self.locker.is_premium` else `free_plan_storage_days`) instead of the literal `30`. Same lookup replaces the hardcoded `30` in `apps/payments/services.py::ensure_storage_fee_for_parcel`. Centralize this in one place (e.g. a `Parcel.free_storage_days` property) so both call sites read it, since they already independently duplicate the constant today.

Migrations: one migration each in `apps.accounts`, `apps.notifications`, `apps.payments`.

## Templates

Create:
- `templates/accounts/plan.html` — plan comparison (Free vs Premium perks table), current plan badge, "Upgrade to Premium" button + Razorpay checkout JS (mirror the pattern in `templates/shipments/detail.html`)

Modify:
- `templates/accounts/profile.html` (or wherever locker/account nav lives) — add a "Plan" link
- `templates/shipments/*` service-tier selection partial — show discounted price + "Premium discount applied" note when `request.user.locker.is_premium`

Static assets: none beyond existing `static/css/main.css` variables.

## Files to change

- `apps/accounts/models.py` — add `plan`, `plan_expires_at`, `is_premium` to `Locker`
- `apps/accounts/urls.py` — add `plan/` route
- `apps/accounts/views.py` — add `PlanView`
- `apps/notifications/models.py` — add plan-config fields to `AppSettings`
- `apps/payments/models.py` — add `payment_type` to `Payment`
- `apps/payments/views.py` — add `CreatePlanOrderView`; extend `VerifyPaymentView` and `RazorpayWebhookView`'s `payment.captured` branch to call `_activate_premium_plan(payment)` when `payment.payment_type == 'plan_upgrade'`
- `apps/payments/urls.py` — add `plan/upgrade/order/` route
- `apps/payments/services.py` — replace hardcoded `30` in `ensure_storage_fee_for_parcel` with the plan-aware free-days lookup
- `apps/locker/models.py` — replace hardcoded `30` in `Parcel.days_remaining_free` (and related properties) with the same lookup, factored into one property
- `apps/shipments/views.py` — apply `premium_shipping_discount_percent` to `rate.calculate_price(...)` at both call sites (`_get_service_options` and the service-tier POST handler around line 536) via one small shared helper to avoid duplicating the discount math
- `indiabox/dashboard.py` / relevant `admin.py` — expose new `AppSettings` and `Locker.plan` fields in django-unfold admin

## Files to create

- `apps/accounts/views.py` → `PlanView` (added to existing file, not a new file)
- `templates/accounts/plan.html`
- Migrations: `apps/accounts/migrations/00XX_locker_plan.py`, `apps/notifications/migrations/00XX_appsettings_plan_config.py`, `apps/payments/migrations/00XX_payment_payment_type.py`

## New dependencies

"No new dependencies."

## Rules for implementation

- Use Django ORM only
- No raw SQL unless absolutely necessary
- Parameterised queries only if raw SQL is unavoidable
- `PlanView` and any locker-owned data use `LoginRequiredMixin` + read via `request.user.locker` (1:1), consistent with existing account views — no cross-user access is possible without an ownership mixin since `Locker` is looked up off `request.user`
- Security logging through the `security` logger for plan purchase success/failure (mirror `VerifyPaymentView`'s `logger.info`/`logger.warning` calls)
- CSS variables from `static/css/main.css` only
- `templates/accounts/plan.html` extends `templates/base.html`
- Reuse `RazorpayService.create_order` / `verify_payment_signature` / `verify_webhook_signature` as-is — do not add a second Razorpay integration path
- `CreatePlanOrderView` must guard against duplicate orders the same way `CreatePaymentOrderView` does (recent pending `Payment` with `payment_type='plan_upgrade'` in the last 30 minutes returns the existing order instead of creating a new one)
- `_activate_premium_plan(payment)` must be idempotent (checking `payment.status != 'captured'` before applying, exactly as the existing webhook handler already guards shipment payments) so a webhook retry or the client-side verify call racing the webhook doesn't double-extend `plan_expires_at`
- Upgrading while already Premium extends from `max(locker.plan_expires_at, now())`, not from `now()`, so an early renewal doesn't discard remaining paid time
- Free-days and shipping-discount values must always be read from `AppSettings.load()` at the moment they're needed (already-cached via the existing `get_settings()`/`load()` pattern) — never re-hardcode a plan constant in view/model code

## Definition of done

- [ ] Migrations for `apps.accounts`, `apps.notifications`, `apps.payments` apply cleanly on a fresh DB and on top of existing data
- [ ] New user's `Locker` defaults to `plan='free'`, `is_premium` is `False`
- [ ] `/account/plan/` loads, shows current plan and a Free-vs-Premium comparison using live `AppSettings` values
- [ ] Clicking "Upgrade to Premium" creates a Razorpay order for `AppSettings.premium_plan_price` and opens the Razorpay checkout
- [ ] Completing a test payment sets `locker.plan='premium'` and `plan_expires_at = now + premium_plan_duration_days`; page reflects Premium status without a manual refresh loop
- [ ] Retrying/duplicating the webhook call for the same order does not extend `plan_expires_at` a second time
- [ ] A parcel on a Premium locker shows `days_remaining_free` computed from `premium_plan_storage_days`; a Free locker's parcel uses `free_plan_storage_days`
- [ ] `StorageFee` generation (`ensure_storage_fee_for_parcel`) respects the same plan-aware free-days figure — a Premium locker's parcel does not get charged before the extended window elapses
- [ ] Shipment service-tier price shown to a Premium user reflects `premium_shipping_discount_percent` off the base `ShippingRate.calculate_price()` value; a Free user sees the undiscounted price
- [ ] Selecting a service tier persists the discounted `shipping_cost` for Premium users (verified via shipment detail page after selection)
- [ ] `AppSettings` plan-config fields (`premium_plan_price`, `premium_plan_duration_days`, `free_plan_storage_days`, `premium_plan_storage_days`, `premium_shipping_discount_percent`) and `Locker.plan`/`plan_expires_at` are editable/visible in the django-unfold admin at `/manage-rb-panel/`
- [ ] Changing an `AppSettings` plan value in admin is reflected on `/account/plan/` and in storage/shipping calculations without a redeploy (cache bust already handled by existing `AppSettings.save()`)
