# Spec: My Profile UI

## Overview
Redesign the existing My Profile page (`accounts:profile`, `templates/accounts/profile.html`) to match the reference design (`static/images/my profile paeg.png`). The reference shows a card-based layout with a profile summary header (avatar, name, verified badge, contact info, account/KYC status, trunk ID), a tabbed section (Personal Information / Security / Addresses / Payment Methods / Preferences), a Personal Information form, a Profile Photo card, an Account Summary card, and a Quick Actions row (Change Password, Two-Factor Auth, Download My Data, Delete Account). On mobile the same content stacks into a single column with the tab bar replaced by an accordion-style section list, matching the pattern established in `02-mytrunk-ui.md`. This is a UI redesign only — no new data being collected beyond what the current form already submits.

## Depends on
`01-dashboard-ui.md`, `02-mytrunk-ui.md` (shared base layout, sidebar/bottom-nav, CSS variable conventions already established).

## App(s) touched
`accounts` only — `ProfileView` (`apps/accounts/views.py:255`) and its template stay in this app. No new app needed.

## Routes
No new routes. Reuses existing `GET/POST /profile/` — `accounts:profile` — `ProfileView` — logged-in — no ownership mixin needed (view already scopes strictly to `request.user`/`request.user.locker`, no `pk`-based lookup of another user's object, consistent with existing code).

## Model changes
No model changes. The reference mockup shows fields not in `apps.accounts.models.User` today: Date of Birth, Country of Residence, a dedicated profile photo, Wallet Balance, and a Two-Factor Auth toggle. These are **out of scope** for this step — the redesigned template must only render fields that exist now (`full_name`, `email`, `phone`, `whatsapp_number`, `date_joined`, `locker.locker_id`, `locker.address`, `locker.phone`, KYC status via `apps.accounts.models.KYCDocument`). "Quick Actions" that have no backing view yet (Two-Factor Auth, Download My Data, Delete Account) render as visually present but disabled/"Coming soon" — do not fabricate endpoints for them. Change Password is already covered by the existing Django auth password-change flow if present; verify against `apps/accounts/urls.py` before wiring the link, otherwise link disabled with "Coming soon".

## Templates
- **Create:** none.
- **Modify:** `templates/accounts/profile.html` — full markup rewrite: profile summary header card, tab/accordion section nav (Personal Information active by default; other tabs can be static placeholders if no backing feature exists yet — do not delete the working Personal Information form and Sign Out action), two-column desktop layout (form + photo/account-summary sidebar) collapsing to single column stacked layout under the mobile breakpoint used in `my_trunk.css`.
- New/modified static assets: `static/css/accounts.css` (new — profile-specific styles) or extend `static/css/my_trunk.css`'s responsive patterns; check which file `profile.html` currently loads before deciding. Use existing CSS variables only (`--primary`, `--accent`, `--surface-*`, `--text-*`, `--radius-*`, `--shadow-*`).

## Files to change
- `templates/accounts/profile.html`
- `static/css/main.css` or a new `static/css/accounts.css` (whichever the template ends up loading — confirm current `{% block %}` / static includes in `templates/base.html` first)

## Files to create
- `static/css/accounts.css` (only if profile styles don't fit cleanly into an existing stylesheet — prefer reusing `my_trunk.css` breakpoint patterns over duplicating them)

## New dependencies
No new dependencies.

## Rules for implementation
- Use Django ORM only, no raw SQL unless there's no ORM equivalent
- Parameterised queries only if raw SQL is unavoidable
- Any authenticated view touching user-owned data must use one of the ownership mixins in `indiabox/mixins.py`, not a hand-rolled check — not applicable here since `ProfileView` already only touches `request.user`, but do not introduce any new lookup by another user's `pk` without adding one
- Security-relevant actions (e.g. any future password change, delete account) log through the `security` logger
- Use CSS variables from `static/css/main.css` — never hardcode hex values
- Uploaded files (a future profile photo upload) go to Supabase Storage, not local `MEDIA_ROOT` — do not implement photo upload in this step unless the existing form already supports it; check first
- All templates extend `templates/base.html`
- Do not fabricate backend behavior for Quick Action links that have no view — render them visibly but non-functional ("Coming soon") rather than dead/broken links
- Match reference image structure exactly for both breakpoints — mobile is not a simplified version, it's the same content in a stacked/accordion layout

## Definition of done
- [ ] `python manage.py runserver`, log in, visit `/profile/` on a desktop-width viewport — layout matches reference: header card with avatar/name/status pills, tab row, two-column body (form left, photo + account summary right)
- [ ] Resize to mobile width (or use browser device toolbar) — layout matches reference: stacked header card, accordion-style section list, quick actions list, no horizontal scroll/overflow
- [ ] Existing "Edit Profile" form still submits and updates `full_name`, `phone`, `whatsapp_number` correctly (POST still works unchanged)
- [ ] Sign Out button still works
- [ ] WhatsApp support link still works
- [ ] No console errors, no broken/dead-looking links presented as functional
- [ ] KYC status and locker/trunk ID render correctly from real data (no hardcoded placeholder values left in the template)
