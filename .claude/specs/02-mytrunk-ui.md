# Spec: My Trunk UI

## Overview
Redesign the My Trunk page (`locker:my_locker`, `templates/locker/my_trunk.html` + `static/css/my_trunk.css`) to match the reference mockup (`static/images/my truck ui.png`) — a stat-tile summary row (Ready to Ship / Approval Pending / Returns / Discarded), a search + category filter + sort/view-toggle toolbar, and an item list that renders identically in structure between desktop (multi-column grid) and mobile (single-column list), per the user's explicit ask that "mobile and desktop [have the] same" layout as the reference. This is a UI-only pass: no new data, models, or routes — all four categories already exist as `MyTrunkView` tabs (`action_required`, `ready_to_ship`, `returns`, `discards`) and their counts already come from `_get_locker_tab_counts`.

## Depends on
None directly, but reuses the CamelTrunk visual language established in [[01-dashboard-ui]] (`--ct-*` variables, card/badge patterns in `static/css/dashboard.css` and shared chrome in `templates/base.html`).

## App(s) touched
`locker` (template + CSS only — `apps/locker/views.py` `MyTrunkView` already supplies `items`, `total_items`, `active_tab`, `action_count`, `ready_count`, `return_count`, `discard_count`, `page_obj`, `locker`, `has_kyc`). No `accounts`/`shipments`/`content` changes needed.

## Routes
No new routes. Existing routes in `apps/locker/urls.py` (`locker:my_locker`, `locker:action_required`, `locker:ready_to_ship`, `locker:returns`, `locker:discards`) are unchanged — the redesigned stat tiles link to the same `?tab=` query params the current tabs use.

## Model changes
No model changes. Note: the mockup shows a marketplace/retailer badge on each item (Amazon, Myntra, AJIO, Meesho logos) — `Parcel` has no retailer/marketplace field and no such data is captured anywhere in the codebase (verified via grep across `apps/`). This will **not** be fabricated; item cards will use the existing `item_name`/`category`/image data only. If the user wants retailer badges, that requires a separate spec adding a field to `Parcel` plus warehouse-staff admin input for it.

## Templates
- **Modify:** `templates/locker/my_trunk.html` — restructure markup:
  - Replace the pill-style `.trunk-tabs` row with four stat tiles (numbered icon, label, count) matching the mockup, still linking to `?tab=action_required` etc. (`action_required` tile is labeled "Approval Pending" to match the mockup's wording; `ready_to_ship` → "Ready to Ship"; `returns` → "Returns"; `discards` → "Discarded").
  - Add a toolbar row: search input (client-side JS filter over rendered card titles — no new backend search endpoint), a category `<select>` (client-side filter using each card's existing `item.parcel.category`/`get_category_display`), the existing "Filter" affordance, a sort dropdown (client-side re-sort of visible cards by date, since `items` is already date-sorted server-side — no new query param needed beyond what's already sortable in-memory), and a grid/list view toggle (CSS class swap on the container, persisted via `localStorage` like the dashboard's announcement toggle).
  - Keep the "Select All" + "Ship Selected" toolbar functionality intact (currently `.trunk-toolbar`), integrated into the new toolbar layout rather than removed.
  - Card layout must use one shared partial/markup structure for both breakpoints — CSS Grid switches from multi-column (desktop) to single-column (mobile) via `grid-template-columns`, not two separate DOM structures, so "mobile and desktop are the same" per the user's ask (same component, same fields, same order — only column count and information density via CSS change).
  - Keep the empty-state block unchanged.
- **Modify:** `static/css/my_trunk.css` — extend the existing breakpoint set (768px / 640px / 380px already present) rather than introducing a new system; add stat-tile, toolbar, search-input, and list-view styles. Reuse `--ct-*` variables from `dashboard.css` where applicable for visual consistency with the CamelTrunk skin, and `--primary`/`--accent`/`--surface-*`/`--radius-*`/`--shadow-*`/`--text-*` from `static/css/main.css` — never hardcode new hex values.
- No changes needed to `templates/base.html` or `static/css/main.css` (shared chrome already responsive per [[01-dashboard-ui]]).

## Files to change
- `templates/locker/my_trunk.html`
- `static/css/my_trunk.css`

## Files to create
None expected.

## New dependencies
No new dependencies.

## Rules for implementation
- Use Django ORM only, no raw SQL unless there's no ORM equivalent (not expected — no query changes anticipated).
- `MyTrunkView` already uses `LoginRequiredMixin` and scopes all queries through `request.user.locker`; don't touch `apps/locker/views.py` — this is a template/CSS-only spec.
- Any authenticated view touching user-owned data must use one of the ownership mixins in `indiabox/mixins.py` — no new views are being added here, so this doesn't introduce new surface, but don't regress the existing `LoginRequiredMixin` usage.
- Security-relevant actions log through the `security` logger — n/a here (no new POST actions).
- Use CSS variables from `static/css/main.css` and `--ct-*` variables already defined in `dashboard.css` — never hardcode new hex values.
- Template extends `templates/base.html` via `{% block content %}` — keep as-is.
- Mobile responsiveness mandatory at minimum: 768px, 640px, 380px (match the existing breakpoint set in `my_trunk.css`). No horizontal scroll or clipped/overlapping text at any viewport ≥ 360px wide.
- Desktop and mobile must render the same component structure (stat tiles, toolbar, item cards in the same order with the same fields) — differences are CSS-driven (columns, spacing, font-size), not separate template branches, per the user's explicit "make the ui of mobile and desktop same" instruction.
- Search/filter/sort/view-toggle are client-side only (vanilla JS operating on already-rendered DOM) — no new backend endpoints, since `MyTrunkView` already returns all of a tab's items server-side rendered/paginated.
- Preserve existing "Select All" / "Ship Selected" checkbox behavior for `ready_to_ship` items — don't regress it while restructuring the toolbar.
- Don't fabricate the mockup's retailer/marketplace badges (Amazon/Myntra/AJIO/Meesho) — no data source exists for this (see "Model changes" above).

## Definition of done
- `python manage.py runserver`, log in, load `/locker/` (My Trunk) at desktop width (≥1280px) — stat tiles, toolbar (search/category/sort/view-toggle), and item grid render without visual regressions, matching the reference mockup's desktop panel.
- Resize/DevTools-emulate at 768px, 640px, 380px, 360px — no horizontal scroll, no overlapping/clipped text, tap targets stay usable size; the mobile layout matches the reference mockup's mobile panel (stat tiles as a 2x2 or scrollable row, single-column item list).
- Confirm stat tiles link to the correct filtered tab (`?tab=action_required`, `?tab=ready_to_ship`, `?tab=returns`, `?tab=discards`) and counts match `_get_locker_tab_counts` output for a test user with items in each category.
- Search input filters visible item cards by title as you type; category select filters by category; sort dropdown re-orders visible cards; grid/list toggle switches layout and persists across reload via `localStorage`.
- "Select All" checkbox still checks/unchecks all `ready_to_ship` item checkboxes; "Ship Selected" link still navigates to `shipments:create`.
- Pagination controls still work when a tab has more than 20 items.
- Empty-state message still displays correctly when a tab has zero items.
- Sidebar/mobile bottom-nav "My Trunk" entry still highlights/functions correctly when on this page.
- No new console errors in browser DevTools on page load at any tested width.
