# Spec: Login Page

## Overview
Redesign the login (`accounts:login`) and verify-OTP (`accounts:verify_otp`) pages to match the reference design (`static/images/login page.png`), which shows a two-pane desktop layout (site header + marketing panel with feature highlights + login card) and a single floating-card mobile layout, using the same visual language across breakpoints. This is a UI-only redesign: the passwordless email-OTP flow (Supabase `sign_in_with_otp` / `verify_otp`) stays functionally unchanged. The reference shows a Mobile/Email tab switcher, Google/Apple social buttons, and 6-box OTP input — these are rebuilt visually, but only Email login and the existing Google OAuth stay live; Mobile-number tab and Apple button render disabled/"coming soon" (no phone-auth backend exists, no Apple OAuth wired — confirmed out of scope with user).

## Depends on
None — reuses the existing `apps/accounts` login flow (01-04 specs are unrelated dashboard/profile/shipments UI work).

## App(s) touched
`accounts` only (`templates/accounts/`, `static/css/auth.css`). No new app.

## Routes
No new routes. Existing routes reused as-is:
- `GET/POST /login/` — `accounts:login` — `LoginView` — public
- `GET /login/google/` — `accounts:google_login` — `GoogleLoginView` — public
- `GET/POST /verify-otp/` — `accounts:verify_otp` — `VerifyOTPView` — public (session-token gated, not ownership — no user-owned data touched pre-login)

## Model changes
No model changes.

## Templates
- **Delete and recreate:** `templates/accounts/login.html`, `templates/accounts/verify_otp.html` — per user request, replaced fresh rather than patched.
- **Modify:** `templates/accounts/auth_base.html` — add top marketing header (logo, "Back to Home" / nav, Login/Get Started links) above `.auth-left`, add 4-item feature-highlight row (Safe & Secure / Consolidate & Save / Ship Worldwide / 24/7 Support) below `.auth-copy`, replace static `login-bg.png` photo with a lightweight inline SVG illustration (India pin → dashed route → globe, trunk silhouette) built with existing brand colors — no new binary asset needed.
- **Modify:** `static/css/auth.css` — extend with: `.auth-topbar` (desktop header), `.auth-tabs` / `.auth-tab` (Mobile/Email switcher), `.auth-phone-group` (country-code select + number input, disabled state), `.auth-social-row` / `.auth-social-btn` (Google/Apple buttons, Apple `disabled`), `.auth-feature-grid` / `.auth-feature-grid-item` (4-icon row), `.auth-otp-boxes` (6 separate digit inputs replacing the single OTP field), `.auth-lock-note` (bottom "information is secure" row on verify page). Keep existing `--primary`/`--accent` variables already defined in `.auth-split-layout`; do not hardcode new hex values outside that block.
- No new JS files: OTP-box-to-hidden-field wiring and tab-switch (Mobile tab just shows the disabled state, Email tab shows the real form) are small inline `<script>` blocks in each template, consistent with this codebase having no existing JS build step for auth pages.

## Files to change
- `templates/accounts/auth_base.html`
- `static/css/auth.css`

## Files to create
- `templates/accounts/login.html` (fresh, replacing deleted version)
- `templates/accounts/verify_otp.html` (fresh, replacing deleted version)

## New dependencies
No new dependencies.

## Rules for implementation
- Use Django ORM only, no raw SQL unless there's no ORM equivalent (N/A here — no model changes).
- Do not touch `LoginView`/`VerifyOTPView`/`GoogleLoginView` logic in `apps/accounts/views.py` — this spec is templates/CSS only. The OTP form must still POST `email` + `otp_session_token` exactly as today; the 6 digit boxes must combine into the existing single hidden/text `otp` field before submit so `VerifyOTPView.post` needs zero changes.
- Mobile-number tab and Apple button are visually complete but `disabled`/inert (no click handlers wired to real auth) — do not fake a submit path for them.
- "Create an account" text from the reference links back to `accounts:login` (this flow is passwordless — logging in with a new email creates the account), not to a nonexistent signup view.
- Use CSS variables already declared in `.auth-split-layout` (`--primary`, `--primary-hover`, `--primary-light`, `--accent`) plus `static/css/main.css` root variables — never hardcode new hex values.
- All templates continue to extend `templates/accounts/auth_base.html` (which itself is a standalone auth shell, not `templates/base.html` — matches the current pattern, do not change this).
- Mobile layout (`max-width: 1023px`) must visually match the reference's single floating card (rounded corners, shadow, brand header inside the card) — same component styling as desktop, not a stripped-down variant.
- Security-relevant actions already log through the `security`/module logger in `views.py` — unaffected by this UI change.

## Definition of done
- [ ] `python manage.py runserver`, visit `/accounts/login/` (or configured login path) at desktop width (≥1024px): two-pane layout renders — top nav bar, left marketing panel with feature graphic + 4 highlight icons, right card with Mobile/Email tabs, phone input (disabled), Send OTP button, Google/Apple buttons (Apple disabled), "Create an account" link.
- [ ] Same page at mobile width (<600px): single floating card matches desktop's visual style (colors, radius, spacing), no horizontal scroll, all interactive elements reachable and tappable.
- [ ] Email tab is selected by default and functional: submitting a real email still redirects to `accounts:verify_otp` and triggers the existing Supabase OTP email exactly as before.
- [ ] Mobile tab shows the phone UI but does not allow submission (disabled input/button or no-op).
- [ ] `/accounts/verify-otp/` renders 6 separate digit boxes at both breakpoints; typing across boxes and submitting successfully verifies OTP (existing `VerifyOTPView` receives the combined 6-digit `otp` value unchanged).
- [ ] "Resend" and "Change email" links still point to `accounts:login` and work.
- [ ] No console errors in browser dev tools on either page at either breakpoint.
