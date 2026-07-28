# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ShipLocker (project name `indiabox` internally) — Django app for international parcel forwarding: users get a virtual locker address, warehouse staff receive/inspect/photograph incoming parcels, users approve/return/discard them, then request shipment abroad with KYC, customs declaration, Razorpay payment, and multi-carrier (Bluedart/DHL) tracking.

## Commands

```bash
python manage.py runserver              # dev server
python manage.py migrate                 # apply migrations
python manage.py createsuperuser
python manage.py collectstatic --noinput
python manage.py security_check          # custom security audit (apps/accounts/management/commands/security_check.py)
python manage.py backfill_display_ids    # apps/locker
python manage.py sync_tracking           # apps/shipments — polls carrier APIs for tracking updates
python manage.py test_whatsapp           # apps/notifications
```

No test suite exists in this repo (no `tests.py` beyond Django defaults) and no lint/format config — don't assume `pytest`/`ruff`/`black` are wired up unless you add them.

`locustfile.py` at repo root is for load testing (`locust -f locustfile.py`).

Admin panel is served at `/manage-rb-panel/` (obscured URL, not `/admin/`), using django-unfold, configured in `indiabox/settings.py` (`UNFOLD` dict) and `indiabox/dashboard.py`.

## Architecture

**Settings/config**: `indiabox/settings.py` reads everything from env (`.env` via python-dotenv). Database prefers `DATABASE_POOLER_URL` (Supabase pooler, auto-corrected to port 6543) over `DATABASE_URL`, falls back to SQLite if neither set. Redis is used for cache + sessions when `REDIS_URL` is set, otherwise LocMemCache + DB sessions.

**Custom User model**: `apps.accounts.models.User` — UUID pk, email-based auth (no username), integrates with Supabase Auth (`supabase_id` field). OTP (passwordless) login flow lives in `apps/accounts/views.py` + `apps/accounts/services.py` (`SupabaseAuth`), backed by rate limiting middleware.

**Apps** (`apps/`):
- `accounts` — User, Locker (1:1 with User, auto-generates `RB-#####` IDs), KYCDocument, SavedAddress
- `locker` — Parcel lifecycle (received → inspected → approved/returned/discarded), ParcelImage (Supabase Storage), ReturnRequest, DiscardRequest
- `shipments` — Shipment, ShipmentItem (customs declaration), ShipmentDocument, TrackingEvent; carrier integrations under `apps/shipments/services/` via `carrier_factory.py` dispatching to `bluedart_service.py` / `dhl_service.py`
- `kyc` — KYC document upload/verification views
- `content` — static pages, shipping calculator, announcements, shipping zones/rates, admin activity log
- `payments` — Razorpay integration (`apps/payments/services.py` `RazorpayService`, HMAC signature verification), StorageFee automation
- `notifications` — AppSettings (site-wide admin-editable settings singleton, e.g. warehouse address, Razorpay keys, WhatsApp), email/WhatsApp notification services, Django signals

Cross-app pattern: several models pull runtime config from `apps.notifications.models.AppSettings.get_settings()`/`.load()` instead of Django settings, so admins can change things (warehouse address, payment keys) without a redeploy.

**Security layer** (`indiabox/`):
- `middleware.py` — `RateLimitMiddleware` (per-path attempt limits via cache), `SecurityHeadersMiddleware` (CSP, Permissions-Policy — note admin path gets `unsafe-eval` for Alpine.js), `LoginAttemptMiddleware` (lockout logging)
- `mixins.py` — view mixins for ownership enforcement: `UserOwnershipMixin`, `LockerOwnershipMixin` (filters by `request.user.locker`), `ObjectOwnershipRequiredMixin` (404s instead of 403s to avoid revealing existence), `SecureActionMixin` (logs POST actions). Any new authenticated view touching user-owned data should use one of these rather than hand-rolling ownership checks.
- `validators.py` — shared input validators
- Everything security-relevant logs through the `security` logger (console handler, see `LOGGING` in settings)

**Storage**: file uploads (parcel images, KYC docs) go to Supabase Storage (private buckets), not local `MEDIA_ROOT` — see `SUPABASE_URL`/`SUPABASE_KEY` env vars and per-app upload handling.

**Deploy**: Railway (`railway.toml`, `Procfile`) and Render (`render.yaml`, `build.sh`) both supported. `Procfile` release phase runs migrations before web dyno starts; gunicorn runs with 5 gthread workers.
