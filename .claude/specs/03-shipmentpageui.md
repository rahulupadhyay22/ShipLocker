# Spec: Shipments Page UI

## Overview
Redesign the Shipments page (`shipments:list` / `templates/shipments/_list_partial.html`, shared by the `active`/`delivered`/`closed` tab views) to match the reference mockup (`static/images/Shipping page ui.png`) — a stat-tile row (Total Shipments / In Transit / Customs Clearance / Delivered), a search + status + destination + sort toolbar, and a per-shipment row with a horizontal 4-stage progress tracker (Picked Up → In Transit → Customs → Delivered) driven off the existing `Shipment.status` field. Per the user's explicit ask, mobile and desktop must render the **same** component structure — differences are CSS-driven (column count, spacing, stacked stepper) not separate templates. This is a UI-only pass: no new models. The current three-tab (`active`/`delivered`/`closed`) navigation is replaced by a single unified list (all statuses) with a client-side "All Status" filter dropdown, matching the reference exactly — closer to how the mockup actually behaves (one page, one filter, no tabs).

## Depends on
Reuses the CamelTrunk visual language and mobile-parity approach established in [[01-dashboard-ui]] and [[02-mytrunk-ui]] (`--ct-*` variables, stat-tile pattern, shared chrome in `templates/base.html`, single-DOM-structure responsive approach).

## App(s) touched
`shipments` only (views + templates + new CSS file). No `locker`/`accounts`/`payments`/`content` changes.

## Routes
No new routes. Existing routes in `apps/shipments/urls.py` are unchanged:
- `GET /shipments/` — `ShipmentsListView` — becomes the single unified page (all statuses, not just active) — logged-in — already uses `LoginRequiredMixin`, queryset already scoped to `request.user`.
- `GET /shipments/active/`, `/delivered/`, `/closed/` — kept working (still valid URLs, e.g. for old links/bookmarks) but no longer linked from the redesigned UI's nav — the "All Status" dropdown on `/shipments/` covers them client-side.
- `GET /shipments/<uuid:pk>/`, `/shipments/create/`, `/shipments/customs-help/` — unchanged, out of scope.

## Model changes
No model changes. The reference's 4-stage tracker (Picked Up / In Transit / Customs / Delivered) maps to existing `Shipment.STATUS_CHOICES` — no new "milestone date" fields exist or are needed:
- Picked Up → `dispatched_at` (falls back to `created_at` if unset but status has progressed past `packing`)
- In Transit → inferred from status (`in_transit`, `customs`, `out_for_delivery`, `delivered` all imply this stage is complete)
- Customs → inferred from status (`customs`, `out_for_delivery`, `delivered`)
- Delivered → `delivered_at`
Stage completion is a computed property, not stored state. `returned`/`cancelled` shipments render a distinct (non-stepper) badge state instead of a partial tracker, since they fall outside the linear happy path.

## Templates
- **Modify:** `apps/shipments/views.py`:
  - `ShipmentsListView.get_queryset` — return **all** the user's shipments ordered by `-created_at` (not just active-status ones); drop the `tab` context var (no more tabs).
  - `ShipmentStatsMixin` — change aggregate from `active_count`/`delivered_count`/`closed_count` to `total_count`, `in_transit_count` (statuses `in_transit`, `out_for_delivery`), `customs_count` (status `customs`), `delivered_count` (status `delivered`) — matching the reference's four stat tiles.
  - Add a `stage` computed value per shipment for the template (either a model `@property` on `Shipment` in `apps/shipments/models.py` returning `{'picked_up': bool, 'in_transit': bool, 'customs': bool, 'delivered': bool}`, or a template filter — prefer the model property since it's pure derived state, no query cost).
- **Modify:** `templates/shipments/_list_partial.html` — restructure:
  - Replace the dark `virtual-card` hero block with a plain page header ("Shipments" + subtitle) plus a 4-tile stat row (Total Shipments / In Transit / Customs Clearance / Delivered), each tile: icon, count, label, sublabel — matching the reference's tile style (light background, colored icon chip).
  - Remove the `.nav-tabs-container` (Active/Delivered/Closed tabs) entirely.
  - Add a toolbar row: search input (client-side JS filter by `display_id`/tracking number text), status `<select>` ("All Status" + each `STATUS_CHOICES` value, client-side filter by the row's `data-status` attribute), destination `<select>` (client-side filter by `data-country`, populated from distinct countries in the current page's shipments), and a sort `<select>` (client-side re-sort of visible rows: Newest First / Oldest First — rows are already date-sorted server-side so this just needs a client toggle, no new query param).
  - Replace each shipment row's stat grid with the reference's horizontal 4-stage progress tracker (Picked Up / In Transit / Customs / Delivered) with connecting line, filled/unfilled dots, and date labels under each stage, using the new `stage` property; keep the existing action buttons (Pay Now / Track Shipment / View Details) and tracking-number chip below the tracker.
  - Keep the International/Domestic disclaimer alert boxes below the toolbar (unchanged content, may need a "Show details" collapse on mobile per reference's compact mobile panel — check the mockup doesn't show these on the shipments list at all; if the mockup omits them, keep them below the shipment list rather than deleting, since they carry legal/liability text that must not be dropped).
  - Keep the empty-state block, pagination controls, and the existing Razorpay `js-pay-now` script block unchanged.
  - One shared markup structure for both breakpoints — CSS Grid/Flex reflow via media queries only (stat tiles 4-across on desktop → 2x2 or horizontal-scroll row on mobile per reference; toolbar collapses to search + single "Filter" button opening the other selects on mobile, matching the reference's mobile panel which shows just a search bar + "Filter" button).
- **Modify:** `templates/shipments/active.html`, `delivered.html`, `closed.html` — no content changes needed (they already just `{% include 'shipments/_list_partial.html' %}` via their own views' context), but confirm they still render sanely now that the partial no longer has tabs; if they're now redundant given the unified list, leave the views/templates in place (harmless, used by any existing bookmarked links) rather than deleting — not part of this UI pass.
- **Create:** `static/css/shipments.css` — new stylesheet for the stat tiles, toolbar, and progress-tracker styles (currently `_list_partial.html` uses inline styles + ad hoc `<style>` blocks; consolidate new styles here rather than adding more inline/`<style>` soup). Reuse `--ct-*` variables from `dashboard.css` and `--primary`/`--surface-*`/`--radius-*`/`--shadow-*`/`--text-*`/`--success-text` from `main.css` — never hardcode new hex values. Link it from `templates/shipments/list.html` (and `active.html`/`delivered.html`/`closed.html` if they render the partial independently) via a `{% block extra_css %}` (check `base.html` for the correct block name before adding).

## Files to change
- `apps/shipments/views.py`
- `apps/shipments/models.py` (add `stage` property to `Shipment`)
- `templates/shipments/_list_partial.html`

## Files to create
- `static/css/shipments.css`

## New dependencies
No new dependencies.

## Rules for implementation
- Use Django ORM only, no raw SQL unless there's no ORM equivalent (not expected — this is an aggregate/property change only).
- Parameterised queries only if raw SQL is unavoidable (n/a).
- `ShipmentsListView` and friends already use `LoginRequiredMixin` and scope querysets through `request.user` — don't introduce a hand-rolled ownership check; if any new query is added, keep it scoped to `Shipment.objects.filter(user=self.request.user, ...)`.
- Security-relevant actions log through the `security` logger — n/a (no new POST/security-relevant actions in this pass).
- Use CSS variables from `static/css/main.css` and `--ct-*` variables from `dashboard.css` — never hardcode new hex values in `shipments.css` or inline styles.
- Uploaded files (parcel images, KYC docs) go to Supabase Storage — unaffected; the shipment row's thumbnail already reads `parcel.images.first.image_url`, don't change that.
- All templates extend `templates/base.html` — `_list_partial.html` is included by templates that already do this; don't change that chain.
- Desktop and mobile must render the same DOM structure (stat tiles, toolbar, shipment rows with the same fields/stepper in the same order) — differences are CSS media-query driven only, per the user's explicit "make the ui of mobile and desktop same" instruction. No horizontal scroll or clipped/overlapping text at any viewport ≥ 360px wide; match the existing breakpoint set used in `my_trunk.css`/`dashboard.css` (768px / 640px / 380px) unless the reference mockup clearly needs an additional one.
- Search/status/destination/sort filtering is client-side only (vanilla JS over already-rendered rows) — no new backend query params, consistent with how [[02-mytrunk-ui]] implemented its toolbar.
- Don't fabricate carrier live-tracking data — the "Track Shipment" action keeps its existing behavior (external carrier tracking link), the progress tracker only reflects `Shipment.status`/timestamps already in the database, not real carrier milestones.
- Preserve the existing Razorpay pay-now flow (`js-pay-now` button + inline script in `_list_partial.html`) exactly — don't refactor it while restructuring the surrounding markup.

## Definition of done
- `python manage.py runserver`, log in, load `/shipments/` at desktop width (≥1280px) — stat tiles (Total/In Transit/Customs/Delivered with correct counts), toolbar (search/status/destination/sort), and shipment list with horizontal progress trackers render matching the reference mockup's desktop panel.
- Resize/DevTools-emulate at 768px, 640px, 380px, 360px — no horizontal scroll, no overlapping/clipped text; stat tiles and toolbar collapse sensibly; each shipment row's stepper remains legible; layout matches the reference mockup's mobile panel.
- Search input filters visible rows by shipment ID/tracking number as you type; status select filters by status; destination select filters by country; sort select re-orders visible rows.
- Progress tracker correctly reflects each test shipment's actual status (e.g. a `customs` shipment shows Picked Up + In Transit + Customs filled, Delivered unfilled; a `delivered` shipment shows all four filled).
- `returned`/`cancelled` shipments still display sensibly (their own badge, no broken/partial tracker).
- Pay Now / Track Shipment / View Details buttons still work exactly as before (Razorpay modal opens, verify call succeeds, tracking link opens carrier search, detail page navigates).
- Pagination still works with more than 20 shipments across all statuses combined (since the list is no longer status-filtered server-side).
- Empty-state message still displays when the user has zero shipments.
- Sidebar/mobile bottom-nav "Shipments" entry still highlights/functions correctly on this page.
- No new console errors in browser DevTools on page load at any tested width.
