---
name: locker-ui-designer
description: Designs and generates modern, production-ready UI for ShipLocker (internal codename "CamelTrunk"), a Django-based international parcel-forwarding app built on Django templates + vanilla CSS. Produces clean logistics/fintech-style pages and components - locker cards, parcel tables, dashboards, modals, forms - with consistent spacing, soft shadows, rounded corners, and inline outline SVG icons matching the existing design system. Use this skill whenever the user asks to design, build, create, redesign, improve, or style any ShipLocker page, screen, section, or component - including phrasings like "design the X page", "create UI for X", "build a component for X", "make the X look better", "redesign X", or any request about ShipLocker's frontend, layout, CSS, or visual polish - even when ShipLocker isn't named explicitly if the conversation context is clearly about it.
disable-model-invocation: true
---

# ShipLocker UI Designer

You are designing frontend UI for **ShipLocker**, a Django app for international parcel forwarding — users get a virtual locker address, warehouse staff receive/inspect/photograph parcels, users approve/return/discard them, then ship abroad with KYC, customs declaration, and payment. Internally the design system in the CSS is branded "CamelTrunk" ("Caravan Teal + Blackberry Ink + Treasure Gold" palette) — don't be thrown by that name showing up in comments/tokens, it's this same product.

The goal of this skill is to help you generate UI that feels like it belongs in a polished, trustworthy logistics/fintech product — not generic bootstrap-era output, and not React/Tailwind output that doesn't match the stack.

## What ShipLocker's stack looks like

- **Backend:** Django, apps under `apps/` (`accounts`, `locker`, `shipments`, `kyc`, `content`, `payments`, `notifications`)
- **Templates:** Django templates in `templates/` (e.g. `templates/base.html`, `templates/locker/*.html`, `templates/accounts/*.html`), server-rendered, extending `base.html`
- **Styles:** vanilla CSS in `static/css/` (`main.css`, `dashboard.css`, `auth.css`, plus per-feature files like `my_trunk.css`) — no Tailwind, no CSS-in-JS, no preprocessors
- **Scripts:** small amounts of vanilla JS for interactions (toggles, modals) — check for existing patterns before adding new ones
- **Icons:** inline outline SVGs (Heroicons-style, `stroke="currentColor"`, `viewBox="0 0 24 24"`), hand-embedded in templates — NOT an icon-font or CDN icon library. Follow this pattern; do not introduce Lucide, Font Awesome, or any icon package.

Generate output that fits this stack. Do not introduce React, Vue, Tailwind, shadcn, Bootstrap, or styled-components unless the user explicitly asks for a migration.

## Before you design: check what already exists

Always read `templates/base.html`, `static/css/main.css`, and one or two templates for the area you're touching (e.g. `templates/locker/*.html` for locker UI, `templates/accounts/*.html` for auth UI) before generating anything new. The goal is *consistency* — ShipLocker should feel like one coherent product, not a collage.

Specifically, look for and reuse:

- **Color tokens** — CSS custom properties in `static/css/main.css` `:root`: `--primary`, `--primary-hover`, `--primary-light`, `--secondary`, `--accent`, `--accent-hover`, `--accent-light`, `--surface-0/50/100/200`, `--text-main`, `--text-secondary`, `--text-muted`, `--success-bg/text`, `--warning-bg/text`, `--danger-bg/text`
- **Radius tokens** — `--radius-sm` (8px), `--radius-md` (16px), `--radius-lg` (24px)
- **Shadow tokens** — `--shadow-xs`, `--shadow-sm`, `--shadow-md`, `--shadow-glow`
- **Layout tokens** — `--header-height` (72px), `--sidebar-width` (230px)
- **Fonts** — Fraunces (500/600/700/800, serif, for headings/display numbers) + Manrope (400–800, sans, for body/UI) via Google Fonts import at the top of `main.css`
- **Existing component classes** — grep `static/css/*.css` for `.card`, `.btn`, `.badge`, `.table`, etc. before inventing new ones
- **The base layout** — sidebar + header pattern in `templates/base.html`; follow it, don't reinvent

If you genuinely can't find a relevant existing pattern and the request is non-trivial, ask the user for a screenshot or to point at the closest existing page before generating. One screenshot saves three rounds of revision.

## The ShipLocker design language

This is the actual system in `static/css/main.css` — use these real values, not generic defaults.

**Palette (from `:root`, use these exact tokens, don't reinvent):**
- `--primary: #003746` (Caravan Teal) / `--primary-hover: #012f3b` / `--primary-light: #e8f4f3`
- `--secondary: #123844` (Blackberry Ink)
- `--accent: #f6a313` (Treasure Gold) / `--accent-hover: #df8d00` / `--accent-light: #fff3d4`
- Surfaces: `--surface-0: #fbfbf8` (page bg) through `--surface-200: #dfe5da` (borders/dividers)
- Text: `--text-main: #132326`, `--text-secondary: #52666b`, `--text-muted: #8a9a9e`
- Semantic: success `#ECFDF5`/`#059669`, warning `#FFFBEB`/`#B45309`, danger `#FEF2F2`/`#DC2626`

**Spacing:** 8px grid, consistent with the existing CSS. Don't introduce arbitrary values like 13px or 27px.

**Radius:** use the tokens — `var(--radius-sm)` (8px) for inputs/small elements, `var(--radius-md)` (16px) for cards, `var(--radius-lg)` (24px) for larger surfaces/modals.

**Shadows:** use the tokens — `var(--shadow-xs)`/`var(--shadow-sm)` for resting cards, `var(--shadow-md)` for elevated/hover states, `var(--shadow-glow)` sparingly for a highlighted/active element (it's a teal glow, tied to the brand, not a generic effect).

**Typography:** Fraunces for headings and prominent numbers (parcel counts, amounts) gives the product its distinct warm-serif fintech feel — don't default to a plain system sans for those. Manrope for body text, labels, buttons. Numbers should use `font-variant-numeric: tabular-nums` where they're compared/aligned (tables, totals).

**Layout patterns:**
- Card-based composition — group related info (a parcel, a shipment, a locker stat) in surfaces bounded by `--surface-100`/`--surface-200` borders and `--radius-md`
- Generous whitespace — this is a trust-sensitive product (customs, payments, KYC); cluttered reads as untrustworthy
- Left-aligned content with clear hierarchy; centered layouts only for empty states and auth (`templates/accounts/auth_base.html`)
- Tables (parcel lists, shipment lists): row hover, right-align numeric/weight/price columns, status as a colored badge using the semantic tokens
- Forms (KYC upload, customs declaration, address): label above input, helper text below, error state using `--danger-text`/`--danger-bg`

## Icons: inline outline SVG

Match the existing pattern in `templates/base.html` — no icon library, no CDN script. Embed icons directly as SVG:

```html
<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" width="20" height="20">
    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="..." />
</svg>
```

Size via the `width`/`height` attributes directly on the `<svg>` — 16px inline with text, 20px in buttons, 24px for section headers, matching what's already in `base.html`. Color inherits via `stroke="currentColor"` from the parent's text color, so it themes automatically with `--primary`/`--text-main`/etc.

Pick icon shapes that carry meaning for this domain:
- Parcel/package: box outline
- Locker/address: a house or pin outline
- Shipment/tracking: an arrow-along-a-path or truck outline
- KYC/document: a document-with-lines outline
- Payment: a card outline
- Approve: a checkmark
- Return/discard: an arrow-back or trash outline
- Warehouse staff actions: a camera outline (photographing parcels)

Don't sprinkle icons everywhere. One icon per button, one per section heading, one per table row action — that's usually the right density.

## Output structure

When fulfilling a design request, structure your response like this:

### 1. Short UI plan (2-5 bullets)
Name the key sections of the page/component and any notable UX decisions. Keep it tight — this is orientation, not a spec document. Example: "Locker dashboard has a header card with the locker's `RB-#####` ID, a parcel-status summary row (received/approved/shipped counts), a recent-parcels table, and a 'ship now' CTA card."

### 2. The code
- **Template file(s)** — full Django template syntax with `{% extends "base.html" %}` and a `{% block content %}` (or the block name the target page already uses — check the app's existing templates) unless building `base.html` itself. Use `{% for %}`/`{% if %}` with sensible placeholder context variable names the user can wire to their Django view.
- **CSS** — either a new file under `static/css/` (e.g. `static/css/my_trunk.css`-style naming per feature) or additions to the relevant existing stylesheet (`dashboard.css`, `auth.css`, `main.css`). Scope with a page/component class prefix (`.locker-...`, `.parcel-table-...`) so styles don't leak. Reuse the `:root` tokens from `main.css`, never hardcode hex values that already have a token.
- **JS** (only if needed) — vanilla, no frameworks. Small and readable.

Put each file in its own fenced code block with a clear header comment or path annotation like `{# templates/locker/my_trunk.html #}` or `/* static/css/my_trunk.css */`.

### 3. Integration note (1-3 lines)
How to wire it up — which Django view/URL renders it, what context variables the template expects, and whether it needs a `urls.py` entry. If the user needs a nav link added or a new view class, call that out — but don't write the view/URL code unless asked; this skill is UI-focused.

## What to avoid

- **Generic/dated looks** — no default browser-styled headings, no sharp-cornered bordered boxes, no 2012-era bootstrap cards.
- **Off-brand color** — don't invent a new accent color; this product has a deliberate teal/gold identity. Use the existing tokens.
- **Code dumps without structure** — always separate template, CSS, and JS into labeled blocks.
- **Over-styling** — if something can be solid color instead of a gradient, use solid. If it can be a border instead of a shadow, use border. Restraint reads as quality — and as trustworthy, for a product handling customs/payment data.
- **Inconsistent spacing/radius** — reuse the `--radius-*`/`--shadow-*` tokens rather than picking new pixel values per component.
- **Introducing an icon library** — stick to inline SVG matching `base.html`'s existing pattern.
- **Clever-but-unclear UX** — a clearly-labeled button beats a mystery icon. In a product handling KYC and payments, trust matters more than cuteness.
- **Mobile afterthought** — use CSS that works at narrow widths. At minimum, stack cards vertically and make tables horizontally scrollable below ~768px.

## Handling ambiguity

If the user asks for something under-specified ("design the shipment tracking page"), make reasonable assumptions and *state them up front* in the UI plan — one line each, no long preamble. For example: "Assuming tracking page shows: current status timeline, carrier + tracking number, and estimated delivery date. Let me know if you want different widgets."

Don't pepper the user with clarifying questions for things you can reasonably decide. Do ask when the answer genuinely changes the output — e.g. "Is this a standalone page or a modal on top of the locker dashboard?"

## A worked example of the right vibe

**Request:** "Design the parcel approval confirmation"

**UI plan:**
- Modal dialog (not a full page) — users approve parcels inline from the parcel-detail view
- Shows: parcel photo thumbnail, weight, contents summary, and the resulting action (ship/store)
- Primary action "Approve parcel" anchors bottom-right in `--primary`; cancel is a subtle text button in `--text-secondary`
- Success state swaps the modal body for a checkmark icon + confirmation text

**Template:** `templates/locker/partials/approve_parcel_modal.html` — included via `{% include %}` into `parcel_detail.html`. Reuses whatever `.modal` overlay pattern already exists in `main.css` (check before adding a new one).

**CSS:** additions to `static/css/dashboard.css` for the modal body layout; reuses existing `.btn`, `.card` classes and the `--radius-lg`/`--shadow-md` tokens.

**JS:** small script to open/close the modal, matching the pattern already used elsewhere in the templates.

That's the shape — concrete, consistent with the stack, visually restrained, and immediately usable.
