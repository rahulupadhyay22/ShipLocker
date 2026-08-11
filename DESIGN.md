---
name: CamelTrunk
description: International parcel forwarding platform — the operator's ledger for a shopper's caravan of parcels
colors:
  caravan-teal: "#003746"
  caravan-teal-hover: "#012f3b"
  caravan-teal-light: "#e8f4f3"
  blackberry-ink: "#123844"
  treasure-gold: "#f6a313"
  treasure-gold-hover: "#df8d00"
  treasure-gold-light: "#fff3d4"
  surface-0: "#fbfbf8"
  surface-50: "#f5f6f2"
  surface-100: "#eaeee7"
  surface-200: "#dfe5da"
  text-main: "#132326"
  text-secondary: "#52666b"
  text-muted: "#8a9a9e"
  text-on-dark: "#fff8e8"
  success-bg: "#ECFDF5"
  success-text: "#059669"
  warning-bg: "#FFFBEB"
  warning-text: "#B45309"
  danger-bg: "#FEF2F2"
  danger-text: "#DC2626"
  badge-danger: "#e11d48"
typography:
  display:
    fontFamily: "Fraunces, serif"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Manrope, sans-serif"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "Manrope, sans-serif"
    fontWeight: 700
    letterSpacing: "0.05em"
rounded:
  sm: "8px"
  md: "16px"
  lg: "24px"
  pill: "999px"
components:
  button-primary:
    backgroundColor: "{colors.treasure-gold}"
    textColor: "#003746"
    rounded: "{rounded.sm}"
    padding: "0.68rem 1.15rem"
  button-primary-hover:
    backgroundColor: "{colors.treasure-gold-hover}"
  button-secondary:
    backgroundColor: "#ffffff"
    textColor: "{colors.text-secondary}"
    rounded: "{rounded.sm}"
    padding: "0.68rem 1.15rem"
  card:
    backgroundColor: "{colors.surface-0}"
    rounded: "12px"
    padding: "1rem 1.1rem"
---

# Design System: CamelTrunk

## Overview

**Creative North Star: "The Caravan Ledger"**

CamelTrunk's interface reads as an official expedition ledger: a deep teal-and-navy operator's book with gold seals marking the things that matter. The dominant surfaces are near-black teal (`#003746` "Caravan Teal") and a deeper ink (`#123844` "Blackberry Ink"), with a warm treasure-gold (`#f6a313`) reserved for accents, active states, and calls to action — the wax-seal moment against an otherwise disciplined, procedural canvas. Cream-white surfaces (`#fbfbf8`–`#eaeee7`) and generous rounding (8–24px) keep the ledger from feeling cold or bureaucratic; this is a serious operator handling your parcels and money, but not a sterile one.

The system is dashboard-first and dense by design: stat tiles, status badges, and card grids optimized for scanning operational state (parcel status, shipment tracking, KYC progress) rather than persuading a visitor. Warmth lives in the details — soft shadows, rounded corners, gold-on-cream badge treatments — while trust lives in the structure: consistent card chrome, a fixed teal sidebar, and status color-coding that never varies.

**Key Characteristics:**
- Deep teal/navy base with gold as a rare, meaningful accent — not a decorative wash.
- Warm, rounded geometry (8–24px radii) softens an otherwise official, procedural tone.
- Dense, scannable dashboard layout: stat tiles, status badges, card grids.
- Serif display type (Fraunces) for headings against a clean sans body (Manrope) — ledger authority paired with everyday legibility.

## Colors

A restrained teal-and-gold palette: navy-teal for structure and trust, gold for the rare moment that needs to stand out, warm neutrals for everything in between.

### Primary
- **Caravan Teal** (`#003746`): Sidebar background, primary text on light surfaces via contrast pairing, brand mark background context, primary structural color.
- **Caravan Teal Hover** (`#012f3b`): Hover state for teal-based interactive elements.
- **Caravan Teal Light** (`#e8f4f3`): Light-tint background for icon badges and info alerts on light surfaces.

### Secondary
- **Blackberry Ink** (`#123844`): Secondary structural navy, close in value to primary teal — used where a slightly cooler, deeper tone is needed.

### Tertiary — Treasure Gold
- **Treasure Gold** (`#f6a313`): The single accent color. Primary buttons, active nav state, brand mark fill, seal-of-approval moments.
- **Treasure Gold Hover** (`#df8d00`): Hover/pressed state for gold elements.
- **Treasure Gold Light** (`#fff3d4`): Light-tint background for gold-badged icon containers.

### Neutral
- **Surface 0** (`#fbfbf8`): Default card and content background.
- **Surface 50** (`#f5f6f2`): Secondary surface, form input backgrounds, stat icon backgrounds.
- **Surface 100** (`#eaeee7`): Card borders, dividers.
- **Surface 200** (`#dfe5da`): Secondary button borders.
- **Text Main** (`#132326`): Primary body and heading text.
- **Text Secondary** (`#52666b`): Labels, secondary copy.
- **Text Muted** (`#8a9a9e`): Placeholder and de-emphasized text.
- **Text on Dark** (`#fff8e8`): Text on the teal/navy sidebar and dark panels.

### Semantic
- **Success** (bg `#ECFDF5` / text `#059669`): Approved, delivered states.
- **Warning** (bg `#FFFBEB` / text `#B45309`): Returned states.
- **Danger** (bg `#FEF2F2` / text `#DC2626`, badge `#e11d48`): Action-required, error states.

### Named Rules
**The One Seal Rule.** Treasure Gold marks the single most important action or state on a screen — the primary button, the active nav item, the approved badge. It never floods a layout; its rarity is what makes it read as significant.

## Typography

**Display Font:** Fraunces (serif)
**Body Font:** Manrope (sans-serif)

**Character:** A ledger-authority serif for headings paired with a clean, modern sans for everything operational — the pairing reads as "official document" without tipping into stuffy.

### Hierarchy
- **Display / Headings** (Fraunces, weight 600, `line-height: 1.15`, `letter-spacing: -0.01em`): All `h1`–`h6`, card titles, section headers.
- **Body** (Manrope, weight 400, `line-height: 1.6`): Default body copy, paragraphs.
- **Label** (Manrope, weight 700–800, small size, uppercase where used): Buttons, status badges (`letter-spacing: 0.05em`, uppercase), nav items.

## Layout

Sidebar-driven app shell: a fixed 230px teal sidebar (`--sidebar-width`) on desktop, collapsing behind an overlay on mobile, with a 72px header (`--header-height`). Content area uses grid-based dashboards — a 3-column stat grid (`.dashboard-grid`) and a 1.8fr/1.2fr two-column layout (`.dashboard-layout`) for primary content plus a secondary rail. Cards and tiles space at roughly 1–2.5rem gaps. Transitions favor `cubic-bezier(0.4, 0, 0.2, 1)` for sidebar and nav-item motion.

## Elevation & Depth

Flat-by-default with soft ambient shadows used sparingly to lift interactive surfaces on hover, not as a resting-state decoration. Cards sit nearly flush (`--shadow-sm`) and lift to `--shadow-md` on hover; the virtual locker card and primary buttons get a colored glow shadow tuned to their own hue rather than a generic black shadow.

### Shadow Vocabulary
- **xs** (`0 1px 2px rgba(0,0,0,0.05)`): Barely-there separation for minor elements.
- **sm** (`0 4px 6px -1px rgba(0,0,0,0.05), 0 2px 4px -1px rgba(0,0,0,0.03)`): Resting card elevation.
- **md** (`0 10px 15px -3px rgba(0,0,0,0.05), 0 4px 6px -2px rgba(0,0,0,0.025)`): Hover-lifted cards and stat tiles.
- **glow** (`0 0 24px rgba(0,95,115,0.18)`): Teal ambient glow for emphasis moments.

### Named Rules
**The Response, Not Rest Rule.** Shadows deepen only in response to hover/interaction state; resting surfaces stay close to flat.

## Shapes

Consistently rounded, never sharp: 8px (`--radius-sm`) for buttons, badges, and small chrome; 16px (`--radius-md`) for stat tiles and action tiles; 24px (`--radius-lg`) for the virtual locker card and other hero surfaces. Status badges and the brand mark use full pill radius (`999px`/`100px`). Borders are thin (1px, occasionally 2px on focused form inputs) and low-contrast against their surface.

## Components

### Buttons
- **Shape:** 8px radius (`--radius-sm`), bold uppercase-weight label (font-weight 800, ~0.82rem).
- **Primary:** Treasure Gold background, Caravan Teal text, gold-tinted shadow; hover lifts 2px and deepens the shadow.
- **Secondary:** White background, `surface-200` border, secondary-gray text; hover shifts to `surface-50` background.
- **Danger:** Danger-bg background, danger-text color, no visible border at rest.

### Cards / Containers
- **Corner Style:** 12px radius by default; larger hero cards (virtual locker) use 24px.
- **Background:** `surface-0`, bordered with `surface-100`.
- **Shadow Strategy:** `shadow-sm` at rest, `shadow-md` on hover (see Elevation & Depth).
- **Border:** 1px solid `surface-100`.
- **Internal Padding:** Header and body each pad `1rem 1.1rem`.

### Inputs / Fields
- **Style:** 2px `surface-100` border, 12px radius, `surface-50` background at rest.
- **Focus:** Border shifts to Caravan Teal, background goes white, 4px `primary-light` focus ring (`box-shadow: 0 0 0 4px var(--primary-light)`).

### Status Badges
- **Style:** Pill radius, uppercase label, semantic background/text pairing (approved/delivered = success green, pending/transit = teal, action-required = danger red, returned = warning amber).

### Navigation (Sidebar)
- **Style:** Teal/navy sidebar, `text-on-dark` (`#fff8e8`) labels at 86% opacity, 8px item radius.
- **Hover:** Subtle white-overlay background (`rgba(255,255,255,0.08)`), 2px horizontal shift.
- **Active:** Gold gradient background (`linear-gradient(180deg, #ffb82e, #f3a10f)`) with Caravan Teal text and a gold-tinted shadow — the nav's own "seal" moment.

## Do's and Don'ts

### Do:
- **Do** reserve Treasure Gold for the single most important element per screen (The One Seal Rule).
- **Do** pair Fraunces headings with Manrope body text; don't introduce a third typeface.
- **Do** keep shadows flat at rest and only deepen them on hover/interaction.
- **Do** use full pill radius for status badges and pill-shaped brand elements; use 8–24px scaled radius everywhere else.

### Don't:
- **Don't** use Treasure Gold as a background wash or fill large surface areas with it — it reads as noise, not a seal.
- **Don't** introduce sharp (0px radius) corners; the system has no sharp-edged precedent.
- **Don't** invent new semantic colors outside the established success/warning/danger triad — reuse them.
