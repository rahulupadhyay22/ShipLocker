# Spec: Dashboard UI

> **Scope note (post-implementation):** the original scope below was template/CSS-only.
> Mid-implementation the user shared reference mockup images and asked for closer visual
> parity, which expanded scope into shared chrome and one backend addition. See
> "Actual scope delivered" at the end of this file for what shipped beyond the original plan.

## Overview
Redesign the user dashboard (`accounts:dashboard`, `templates/accounts/dashboard.html` + `static/css/dashboard.css`) so it's fully mobile-responsive and visually consistent with the recently redesigned "My Trunk" page (CamelTrunk visual language). The dashboard is the first screen a logged-in user sees — stat cards, the Trunk hero, insights, recent activity, and quick actions must all reflow cleanly from desktop down to small phones without horizontal scroll or clipped content. This is a UI-only pass: no new data, routes, or models.

## Depends on
None. This is the first tracked spec in this repo (`.claude/specs/` was empty).

## App(s) touched
`accounts` (template + view context only — `apps/accounts/views.py` `DashboardView` already supplies all needed context: `parcels_in_trunk`, `incoming_count`, `total_weight_kg`, `storage_days_left`, `est_shipping_cost`, `trunk_capacity`, `declared_value_total`, `avg_storage_days`, `avg_weight_per_item`, `recent_activity`, `announcements`). No `content`/`locker`/`shipments` changes needed.

## Routes
No new routes. Existing `GET /dashboard/` (`accounts:dashboard`, `DashboardView`, login-required, `LoginRequiredMixin`) is unchanged.

## Model changes
No model changes.

## Templates
- **Modify:** `templates/accounts/dashboard.html` — restructure markup/class names as needed for the redesign (stat row, trunk hero, insights panel, activity list, quick actions, announcements). Keep all `{{ context_var }}` bindings from `DashboardView.get_context_data` intact — do not rename or drop any.
- **Modify:** `static/css/dashboard.css` — this file already has partial responsive breakpoints (1024px / 900px / 640px / 380px) and CamelTrunk navy/gold variables scoped under `body.dashboard-page`. Extend/rework these rather than starting a parallel system. Note: this file was already touched outside this task (uncommitted-then-committed change) — read its current state before editing, don't assume the version described in prior specs.
- No changes needed to `templates/base.html` (sidebar/mobile-header nav chrome is shared and already responsive) or `static/css/main.css` (source of shared CSS variables).

## Files to change
- `templates/accounts/dashboard.html`
- `static/css/dashboard.css`

## Files to create
None expected. If a genuinely dashboard-only visual asset is needed (e.g. an icon), place under `static/img/`.

## New dependencies
No new dependencies.

## Rules for implementation
- Use Django ORM only, no raw SQL unless there's no ORM equivalent (not expected to apply here — no query changes anticipated).
- Any authenticated view touching user-owned data must use one of the ownership mixins in `indiabox/mixins.py` — `DashboardView` already uses `LoginRequiredMixin` and scopes all queries through `user.locker`; preserve this, don't hand-roll new ownership checks.
- Use CSS variables from `static/css/main.css` (`--primary`, `--accent`, `--surface-*`, `--radius-*`, `--shadow-*`, `--text-*`) and the `--ct-*` variables already defined in `dashboard.css` — never hardcode new hex values.
- Template extends `templates/base.html` via `{% block content %}`; keep `{% block body_class %}dashboard-page{% endblock %}` so the CamelTrunk skin stays scoped.
- Mobile responsiveness is mandatory at minimum for these breakpoints: 1024px (tablet), 640px (large phone), 380px (small phone) — match/extend the existing breakpoint set in `dashboard.css` rather than inventing new ones.
- No horizontal scroll or clipped/overlapping text at any viewport ≥ 360px wide.
- Preserve existing JS behavior (`toggleAnnouncements`, announcement show/hide via `localStorage`) — don't rewrite unless the redesign removes the announcements toggle UI, in which case remove the dead JS too.
- Don't touch `apps/accounts/views.py` context data — this is a template/CSS-only spec.

## Definition of done
- `python manage.py runserver`, log in, load `/dashboard/` at desktop width (≥1280px) — stat row, Trunk hero, insights, recent activity, quick actions all render without visual regressions.
- Resize/DevTools-emulate at 1024px, 768px, 640px, 380px, 360px — no horizontal scroll, no overlapping or clipped text, tap targets (buttons, dropdown triggers) stay usable size on mobile.
- Announcements toggle button still works (hide/show, persists via `localStorage` on reload) if kept in the redesign.
- Stat cards, Trunk progress bar, insights grid, recent activity list, and quick action buttons all display correct live data from an authenticated test user with at least one parcel in their trunk.
- Sidebar/mobile-header navigation (hamburger menu, account dropdown) still functions correctly on the dashboard page at mobile widths — confirms `body.dashboard-page` scoping didn't leak into or break shared chrome.
- No new console errors in browser DevTools on page load at any tested width.

---

## Actual scope delivered (post-implementation addendum)

The user shared reference mockup images (`static/images/dashboard-desktop.png`,
`static/images/dashbaord-mobile.png`) partway through implementation and asked for closer
visual parity. That expanded delivered scope beyond the plan above, with explicit user
sign-off per item (via `AskUserQuestion`) before touching shared chrome or the view:

- **`apps/accounts/views.py`** — `DashboardView.get_context_data` gained a `recent_shipments`
  query (`Shipment.objects.filter(user=user)`, annotated with `items_count=Count('items')`,
  last 5, ordered by `-created_at`) to back a new "Recent Shipments" table on the dashboard.
  This is the one line of the original spec's rules ("Don't touch
  `apps/accounts/views.py` context data") that was knowingly broken, by user request.
- **`indiabox/context_processors.py`** — new `nav_counts` processor, wired into
  `indiabox/settings.py` `TEMPLATES.OPTIONS.context_processors`, supplying
  `incoming_parcels_count` (count of the requesting user's `Parcel`s with
  `status='action_required'`) to every authenticated page for the sidebar/bottom-nav badge.
  Fails safe for anonymous users (returns 0, no query).
- **`templates/base.html`** (shared chrome, used on every page — spec originally said
  "no changes needed") — sidebar redesign (new "Incoming Parcels" nav item with live badge,
  Trunk-ID card with copy-to-clipboard, profile card, separated logout form, "Need Help" card
  retained), topbar simplified (dropped the redundant locker-ID pill, now shown in the
  sidebar card instead), and a new fixed mobile bottom tab bar (Dashboard/My Trunk/
  Incoming+badge/Shipments/Account) shown ≤1024px alongside the existing hamburger drawer.
- **`static/css/main.css`** (spec originally said "no changes needed") — new component
  styles backing the above: `.nav-badge`, `.sidebar-trunk-card`/`-value`/`-label`,
  `.sidebar-copy-btn`, `.sidebar-profile-card`/`-avatar`/`-info`/`-name`/`-link`,
  `.sidebar-logout-btn`, `.bottom-tab-bar`/`.tab-item`/`.tab-badge`. New CSS variables added
  to `:root` for dark-surface text/badges (`--text-on-dark`, `--badge-danger`,
  `--danger-text-on-dark`) rather than hardcoding hex in the new rules, keeping the
  "never hardcode new hex values" rule intact for everything added.
- **`templates/accounts/dashboard.html`** — Trunk hero converted from a navy gradient card to
  a white "Trunk Overview" panel (image + items/%/full stats, progress bar, "View My Trunk"
  link) to match the mockup; Recent Activity got a timeline connector line between icons;
  Quick Actions reduced from 3 buttons to 2 (dropped "View My Address" — the mockup's
  replacement action, "Add Incoming Parcel", had no real user-facing route to back it, so by
  user's choice it was dropped rather than fabricated); new Recent Shipments table added,
  backed by the `recent_shipments` context addition above.
- **`static/css/dashboard.css`** — matching styles for the above (`.db-trunk-overview-*`,
  `.db-shipments-table*`), plus dead-code cleanup and `@media` block consolidation
  (duplicate 1024px/640px breakpoints merged) from an earlier pass in this same branch,
  predating the mockup-parity work.

Deliberately **not** built, to avoid fabricating non-existent functionality: the mockup's
country-selector dropdown (no multi-country account feature exists), a numbered
notification-bell badge (no user-facing notification feed exists — `AppSettings` is
admin config, not a user notification model), and profile photos (no avatar upload field
on `User`).

Test coverage for the above: `apps/accounts/tests/test_dashboard_ui.py`.
