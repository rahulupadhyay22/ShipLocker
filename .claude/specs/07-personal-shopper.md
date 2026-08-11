# Spec: TrunkAssist — Personal Shopping Concierge

## Overview

TrunkAssist lets a user ask CamelTrunk staff to source and buy an item on their behalf when they can't buy it themselves — a pasted product link, an uploaded photo/cart screenshot, a named boutique, a named local shop, or a free-form custom ask. A user submits one of 6 typed requests, staff assign an executive, source the item, and issue a quotation; the user approves and pays via the existing Razorpay flow; staff purchase the item, it arrives at the warehouse, and staff link it into the user's existing Parcel/Shipment pipeline for onward shipping. The nav already has a dead "Personal Shopping" link (`templates/base.html:142`) anticipating this feature; no backend for it exists today.

New `apps/personal_shop` app owns the request → quotation lifecycle. It stops owning the item the moment it is "Delivered to Warehouse": at that point staff create/link a `locker.Parcel`, and the item continues through the existing Parcel → Shipment pipeline unmodified — TrunkAssist never re-implements storage, customs declaration, or shipping.

**Out of scope for v1 (explicitly deferred, not silently dropped):** partial fulfillment of multi-item Cart Screenshot requests (one quotation covers the whole request or none of it — no per-line-item partial approval); a dedicated staff console beyond Django admin (see Rules for implementation).

## Depends on

- `01-dashboard-ui.md` (shared chrome, `--ct-*` CSS variables, `templates/base.html`)
- `06-trunk-id.md` (Locker display-ID format, used as the prefix for request display IDs)
- Existing `apps/locker` Parcel model/lifecycle (integration point at "Delivered to Warehouse")
- Existing `apps/payments` Payment model + RazorpayService (integration point for Quotation payment)
- Existing `apps/notifications` AppSettings + signal-based WhatsApp notification pattern

## App(s) touched

- **New**: `apps/personal_shop` (models, views, urls, forms, admin, utils, migrations)
- `apps/payments` — add nullable FK on `Payment`, extend `VerifyPaymentView`
- `apps/notifications` — add WhatsApp template fields on `AppSettings`, add signal receivers
- `indiabox` — register new paths in `RateLimitMiddleware.PATH_CATEGORIES`, register app in `INSTALLED_APPS`/root `urls.py`
- `templates/base.html` — wire the existing dead "Personal Shopping" nav link to the new dashboard route

## Routes

- `GET /personal-shop/` — `TrunkAssistDashboardView` — hub: 6 request-type cards, 3 most recent requests, how-it-works strip, FAQ accordion (static, mirrors `content/faq.html` pattern) — authenticated — `LoginRequiredMixin`
- `GET /personal-shop/requests/` — `PersonalShopRequestListView` — "My Requests" list, filter by `status`/`request_type` query params, `paginate_by = 20` — authenticated — `LockerOwnershipMixin`
- `GET,POST /personal-shop/new/<str:request_type>/` — `PersonalShopRequestCreateView` — one view, `request_type` in `{product_link, image_search, cart_screenshot, boutique_purchase, local_shop_purchase, custom_request}` selects the form class + template from a `TYPE_FORM_MAP`; 404 on unknown type; creates request in `submitted` status (`reviewing` for `custom_request` per spec) — authenticated — `LoginRequiredMixin` (no object yet to own)
- `GET /personal-shop/requests/<uuid:pk>/` — `PersonalShopRequestDetailView` — summary card, timeline stepper (derived from per-transition timestamp fields), uploaded images, notes, activity feed — authenticated — `LockerOwnershipMixin`
- `GET,POST /personal-shop/requests/<uuid:pk>/edit/` — `PersonalShopRequestEditView` — only allowed while `status` is pre-quotation (`submitted`, `reviewing`, `executive_assigned`, `searching`, `needs_info`); 403 otherwise — authenticated — `LockerOwnershipMixin` + `SecureActionMixin`
- `POST /personal-shop/requests/<uuid:pk>/cancel/` — `PersonalShopRequestCancelView` — only allowed pre-`purchased`; sets `status=cancelled`, `cancelled_at` — authenticated — `LockerOwnershipMixin` + `SecureActionMixin`
- `GET /personal-shop/requests/<uuid:pk>/quotation/` — `PersonalShopQuotationView` — line items, fee breakdown, validity countdown; 404 if no quotation exists — authenticated — `LockerOwnershipMixin`
- `POST /personal-shop/requests/<uuid:pk>/quotation/decline/` — `PersonalShopQuotationDeclineView` — sets quotation `status=declined`, request `status=quotation_declined` — authenticated — `LockerOwnershipMixin` + `SecureActionMixin`
- `POST /personal-shop/requests/<uuid:pk>/quotation/pay/` — `CreatePersonalShopPaymentOrderView` — mirrors `payments.CreatePaymentOrderView`: rejects with 400 if `request.active_quotation.is_expired`; otherwise creates `Payment(personal_shop_request=request, quotation snapshot amount, ...)` + Razorpay order, same 30-min duplicate-pending-order guard scoped to `personal_shop_request=request` (i.e. per-request, not per-user — a user with two separate requests can have two concurrent pending orders) — authenticated — `LockerOwnershipMixin` + `SecureActionMixin`
- `GET /personal-shop/requests/<uuid:pk>/payment/confirmation/` — `PersonalShopPaymentConfirmationView` — receipt screen after `payments:verify_payment` redirects back — authenticated — `LockerOwnershipMixin`

No new payment-verification route — `payments.VerifyPaymentView` (already generic, looks up `Payment` by `razorpay_order_id` + `user`) gets one added `elif payment.personal_shop_request:` branch to advance the request to `paid` and stamp `paid_at`, exactly like its existing `if payment.shipment:` branch.

## Model changes

**New app `apps/personal_shop`:**

- `PersonalShopRequest` — UUID pk. `display_id` (`select_for_update` sequential generator identical in shape to `Parcel`'s, format `<locker.display_id>-TA001`). `locker` FK → `accounts.Locker`. `request_type` (6 choices above). `status` (`submitted, reviewing, executive_assigned, searching, quotation_ready, quotation_declined, quotation_expired, payment_pending, paid, purchased, delivered_to_warehouse, added_to_trunk, cancelled, needs_info`). `assigned_executive` FK → `accounts.User` (nullable, `limit_choices_to={'is_staff': True}`, `SET_NULL`). `destination_country`, `budget_min`, `budget_max` (real columns — used for list filtering). `product_url`, `shop_name`, `boutique_name` (real, nullable, blank-able, `db_index=True` columns — the one hot lookup field per type that staff actually search/filter on; populated from whichever form set them, `null` for the types that don't use them; skip the index only if pilot volume never justifies it, this is a deliberate choice not an oversight) alongside `type_details` (`JSONField` — everything else unique to each of the 6 forms, e.g. `quantity`/`size`/`colour` for Product Link, `shop_address`/`shop_phone` for Local Shop). Each type's `ModelForm.clean()` validates its own `type_details` shape (required sub-keys per `request_type`) before save — there is no DB-level JSON schema, validation is form-layer only. `parcel` FK → `locker.Parcel` (nullable, `SET_NULL`) — set when staff mark the item delivered, this is the sole integration point into the existing Parcel/Shipment pipeline. Per-transition timestamp columns (`submitted_at` auto, `executive_assigned_at`, `searching_started_at`, `quotation_ready_at`, `paid_at`, `purchased_at`, `delivered_at`, `added_to_trunk_at`, `cancelled_at`) — same convention as `Parcel.approved_at`/`Shipment` transition timestamps; the request-detail timeline and dashboard activity feed are *derived* from these, no separate activity-log table.
- `PersonalShopImage` — FK `request`, `image_path` (Supabase path, private bucket), `caption`; `image_url` property signs the URL, same shape as `ParcelImage`.
- `PersonalShopNote` — FK `request`, `author` FK → `User`, `message`, `created_at` — executive-to-user notes shown in the detail view's Notes panel.
- `PersonalShopQuotation` — FK `request` (`related_name='quotations'`; re-quotes after decline/expiry create a new row rather than mutating history). `domestic_shipping_amount`, `service_fee_amount`, `payment_gateway_charge`, `subtotal`, `total_amount` (all `DecimalField`). `valid_until`. `status` (`pending, approved, declined, expired`). `is_expired` property (`status == 'pending' and timezone.now() > valid_until`) — checked lazily wherever a pending quotation is read (see Rules), no scheduled task needed. `PersonalShopRequest` also gets `active_quotation` — a nullable FK to `PersonalShopQuotation` (`SET_NULL`), set atomically (inside the same transaction that creates a new quotation and marks any prior one `declined`/`expired`, with the `PersonalShopRequest` row taken via `select_for_update()` first) so "which quotation is live" is never an ambiguous `.latest()` query — belt-and-suspenders with the unique constraint below, whose worst case alone (an `IntegrityError` on the losing writer of a race, not silent corruption) would already be safe without the lock. A `UniqueConstraint` on `PersonalShopQuotation` (`fields=['request'], condition=Q(status='pending')`) is the DB-level backstop against two pending quotations existing at once.
- `PersonalShopQuotationLineItem` — FK `quotation`, `name`, `thumbnail_url` (optional), `variant_details`, `qty`, `unit_amount` (`DecimalField`) — a real table instead of a `line_items` JSONField, so staff edit it as a Django admin `TabularInline` (add/remove rows, numeric fields) rather than hand-typing JSON.

Migration: one initial migration for `apps/personal_shop`.

**`apps/payments/models.py`**: add `personal_shop_request = models.ForeignKey('personal_shop.PersonalShopRequest', null=True, blank=True, on_delete=models.SET_NULL, related_name='payments')` on `Payment` — same nullable-FK shape as the existing `shipment` field. Migration in `apps/payments`.

**`apps/notifications/models.py`**: add `template_personal_shop_executive_assigned`, `template_personal_shop_quotation_ready`, `template_personal_shop_needs_info`, `template_personal_shop_purchased`, `template_personal_shop_added_to_trunk` fields on `AppSettings`, alongside the existing `template_parcel_added`/etc. block. Migration in `apps/notifications`.

## Templates

**Create** (all extend `templates/base.html`, follow the `--ct-*` CSS variables and same-DOM mobile/desktop parity pattern from specs 01–04):
- `templates/personal_shop/dashboard.html` — hub: request-type grid, Your Requests sidebar panel, how-it-works strip, trust badges, FAQ accordion
- `templates/personal_shop/request_list.html` — My Requests, filterable
- `templates/personal_shop/request_form_product_link.html`
- `templates/personal_shop/request_form_image_search.html` (also serves Cart Screenshot as a mode toggle within the same template, per spec's "variant entry point" note)
- `templates/personal_shop/request_form_boutique.html`
- `templates/personal_shop/request_form_local_shop.html`
- `templates/personal_shop/request_form_custom.html`
- `templates/personal_shop/request_detail.html` — summary card, timeline stepper, images/notes/activity panels
- `templates/personal_shop/quotation.html`
- `templates/personal_shop/payment_confirmation.html`
- `templates/personal_shop/_status_pill.html` — shared partial for the `SEARCHING`/`QUOTATION READY`/`PURCHASED`/etc. badges, included from list, detail, and dashboard

**Modify:**
- `templates/base.html` — point the existing "Personal Shopping" nav `href="#"` at `{% url 'personal_shop:dashboard' %}`

**Static assets:**
- `static/css/personal_shop.css` — sidebar-card grid, timeline stepper, quotation table, status pills; reuse `--ct-*` variables from `static/css/main.css`, no new color tokens
- Reuse existing Lucide-style icon set already loaded sitewide (link, image, cart, boutique/bag, store, lightbulb) — no new icon assets

## Files to change

- `indiabox/settings.py` — add `'apps.personal_shop'` to `INSTALLED_APPS`
- `indiabox/urls.py` — `path('personal-shop/', include('apps.personal_shop.urls'))`
- `indiabox/middleware.py` — add `/personal-shop/` path entries to `RateLimitMiddleware.PATH_CATEGORIES` as `'authenticated'`
- `apps/payments/models.py` — add `personal_shop_request` FK to `Payment`
- `apps/payments/views.py` — `VerifyPaymentView.post` — add `elif payment.personal_shop_request:` branch
- `apps/notifications/models.py` — add 3 `template_personal_shop_*` fields to `AppSettings`
- `apps/notifications/signals.py` — add `pre_save`/`post_save` receivers for `personal_shop.PersonalShopRequest`, mirroring the existing `Parcel` old-status-diff pattern
- `templates/base.html` — wire the dead nav link

## Files to create

- `apps/personal_shop/__init__.py`, `apps.py`, `models.py`, `views.py`, `urls.py`, `forms.py`, `admin.py`, `utils.py`
- `apps/personal_shop/migrations/0001_initial.py`
- `apps/payments/migrations/00XX_payment_personal_shop_request.py`
- `apps/notifications/migrations/00XX_appsettings_personal_shop_templates.py`
- Templates and static assets listed above

`apps/personal_shop/utils.py` — `upload_personal_shop_image(file, locker_id, request_display_id)`, same shape as `apps/locker/utils.py:upload_parcel_image` (wraps `SupabaseStorage()`, Pillow compression to 1920×1920 JPEG q80), reusing the existing **`parcel-images`** bucket with path prefix `personal-shop/{locker_id}/{request_display_id}/...` rather than provisioning a new Supabase bucket.

`apps/personal_shop/forms.py` — one `ModelForm` per request type (`ProductLinkForm`, `ImageSearchForm`, `BoutiquePurchaseForm`, `LocalShopPurchaseForm`, `CustomRequestForm`) all writing into `PersonalShopRequest.type_details`; a `TYPE_FORM_MAP = {request_type: (FormClass, template_name)}` dict in `views.py` drives `PersonalShopRequestCreateView`.

`apps/personal_shop/views.py` also defines a plain constant, `EXPECTED_TURNAROUND = {request_type: display_string}` (e.g. `local_shop_purchase: "3–5 business days — requires an in-person visit"`, the rest: `"Usually within 24 hours"`) — a code-level lookup, not a DB field, shown on the request detail page and the confirmation banner after submission.

## New dependencies

No new dependencies.

## Rules for implementation

- Use Django ORM only
- No raw SQL unless absolutely necessary
- Parameterised queries only if raw SQL is unavoidable
- `LockerOwnershipMixin` (not hand-rolled `.filter()`) for every authenticated view touching a `PersonalShopRequest`/related object — it auto-detects the `locker` FK, this is the first app to actually apply it as intended
- Security logging through the `security` logger (`logging.getLogger('security')`) on create, edit, cancel, quotation decline, and payment-order creation — match the existing `f"Secure action: {self.__class__.__name__} by {request.user.email} from ..."` style from `SecureActionMixin`
- CSS variables from `static/css/main.css` (`--ct-*`) only — no new hex values
- Upload files to Supabase Storage via `apps/personal_shop/utils.py`, reusing `SupabaseStorage` and `indiabox.validators.validate_file_upload` exactly as `apps/locker/utils.py` does
- Templates extend `templates/base.html`
- `PersonalShopRequest.display_id` generation must use `select_for_update()` inside the creating transaction, matching `Parcel`'s generator, to stay race-safe under concurrent submissions from the same locker
- Editing a request (`PersonalShopRequestEditView`) is only permitted while `status` is pre-quotation; once a `PersonalShopQuotation` exists for the request, editing is blocked (403) rather than silently allowed — the spec's "locked once quotation is issued" edge case
- Cancellation is blocked once `status` is `purchased` or later (`delivered_to_warehouse`, `added_to_trunk`) — return a 400 with an explanatory message rather than silently no-op
- Quotation payment reuses `RazorpayService` and the Payment model's existing `status` state machine unmodified; do not introduce a second payment-status enum
- Linking to the Parcel pipeline happens only via the `PersonalShopRequest.parcel` FK, set by staff (admin action) when marking `delivered_to_warehouse` — TrunkAssist must never create a `Shipment` or `ShipmentItem` directly; once linked, the item flows through `apps/shipments/views.py:CreateShipmentView` exactly like any other approved parcel
- FAQ content is static in `dashboard.html` (accordion, one open at a time via CSS/`<details>` or minimal JS) — mirrors `apps/content/views.py:FAQView`'s hardcoded-template pattern; no new `Faq` model
- `PersonalShopQuotationView.get` and `CreatePersonalShopPaymentOrderView.post` both call `request.active_quotation.is_expired` and, if true, flip `status='expired'` on the quotation and `status='quotation_expired'` on the request before rendering/rejecting — this is the only expiry mechanism (no Celery beat/cron); a quotation that nobody looks at simply stays `pending` past `valid_until` until the next view load, which is acceptable since payment is blocked either way
- The staff side of this feature (assign executive, write quotation line items, upload images, link a Parcel) is bare Django admin for v1 — acceptable for pilot volume, but a known limitation, not a silent gap: if request volume grows, the line-item `TabularInline` and raw admin forms should be replaced by a dedicated staff console
- Add `apps/personal_shop/tests.py` covering the two places a bug becomes a security or money issue: ownership scoping (a second user's `GET` on someone else's request/detail/quotation/payment URLs returns 404, not 403 or 200) and the status-transition guards (edit blocked once `active_quotation` is set, cancel blocked once `status` is `purchased`+, expired quotation blocks payment) — this repo otherwise has no test suite, these are the exception because they're payment-adjacent

## Definition of done

- [ ] `python manage.py migrate` runs clean; `PersonalShopRequest`, `PersonalShopImage`, `PersonalShopNote`, `PersonalShopQuotation`, `PersonalShopQuotationLineItem` tables exist; `payments.Payment` has `personal_shop_request_id` column; `AppSettings` has the 5 new template fields; the partial unique constraint on `PersonalShopQuotation(request, status='pending')` is present (`\d personal_shop_personalshopquotation` in psql, or attempt to create two pending quotations for the same request via the ORM and confirm it raises `IntegrityError`)
- [ ] "Personal Shopping" nav link in the sidebar navigates to `/personal-shop/` and is no longer `href="#"`
- [ ] Dashboard shows all 6 request-type cards, the 3 most recent requests (or empty state), how-it-works strip, and a working FAQ accordion
- [ ] Submitting each of the 6 forms creates a `PersonalShopRequest` with the correct `request_type`, correct initial `status` (`reviewing` for Custom Request, `submitted` for the rest), a display ID in `<locker-id>-TA###` format, and redirects to the request detail page
- [ ] Uploading a reference image on Image Search / Boutique / Local Shop forms stores it in Supabase (`parcel-images` bucket, `personal-shop/` prefix) and it renders as a signed-URL thumbnail on the detail page
- [ ] Request detail page's timeline stepper reflects only the timestamp fields that are actually set; current step highlighted, future steps show "Pending"
- [ ] "Edit Request" is available before a quotation exists and returns 403 after one is created; "Cancel Request" works before `purchased` and is blocked (400) after
- [ ] A quotation created via Django admin (`assigned_executive`, line items, fees, `valid_until`) is visible at `/personal-shop/requests/<id>/quotation/` with correct Sub Total/Total math and a validity countdown
- [ ] "Approve & Pay" creates a Razorpay order via `CreatePersonalShopPaymentOrderView`, completing payment (test mode) advances `Payment.status` to `captured` and `PersonalShopRequest.status` to `paid` with `paid_at` set, then shows the confirmation/receipt screen
- [ ] "Decline" on the quotation sets `status=quotation_declined` and the UI reflects it without a payment being created
- [ ] My Requests list filters correctly by `status` and `request_type` query params, is paginated at 20/page, ownership-scoped to the logged-in user's locker (a second test user cannot see or `GET` another locker's request/detail/quotation URLs — 404, not 403)
- [ ] A quotation whose `valid_until` has passed shows as `EXPIRED` on next view load (no manual admin action needed) and "Approve & Pay" is blocked with a 400
- [ ] Admin panel (`/manage-rb-panel/`) lists `PersonalShopRequest` with status badges, allows assigning an executive, adding notes, creating a quotation with line items via an inline formset (no hand-typed JSON), and — when marking `delivered_to_warehouse` — linking/creating a `locker.Parcel`, after which the item appears in `CreateShipmentView`'s selectable-parcels list like any normal approved parcel
- [ ] Security logger emits an entry for create/edit/cancel/decline/pay actions (visible in console log output during manual testing)
- [ ] `python manage.py test apps.personal_shop` passes — ownership-scoping and status-transition-guard tests green
- [ ] Layout matches the reference screenshot's visual language (dark navy sidebar, orange accents, white cards, pill status badges) and is usable at both desktop and mobile viewport widths
