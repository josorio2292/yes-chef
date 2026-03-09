# Yes Chef — Design System

> Interface design system for the Yes Chef catering estimation tool.
> Reference: `interface-design` skill.

---

## Intent

- **Who:** Catering company owners and event planners. Practical, time-pressured. Not developers.
- **Task:** Submit a menu → watch ingredients resolve in real-time → get a priced quote for client proposals.
- **Feel:** A high-end professional kitchen at night. Dark, warm, composed. Aged copper fixtures, blackened steel, the amber glow of heat lamps. Calm authority — not cold/corporate, not playful/casual.

## Direction: "The Pass"

A professional kitchen command center rendered in dark luxury. Three views that mirror the kitchen workflow:

1. **Submit** — menu input. Focused card on a dark canvas, like a handwritten prep list under low light.
2. **Kitchen** — live ticket rail. Items moving through stations in real-time, copper accents marking active work.
3. **The Pass** — final quote review. Polished, inspected, ready to hand to the client.

---

## Domain

| Concept | UI mapping |
|---------|-----------|
| Mise en place | Overall layout — organized, nothing extraneous |
| Ticket rail | Kitchen view — items as cards moving through stations |
| The pass | Quote view — final inspection before it goes out |
| Prep station | Decompose step — raw menu → ingredient list |
| 86'd | `not_available` status — kitchen slang for "out of it" |
| Plating | Quote output — the final presentation |

## Palette

Derived from a professional kitchen at night — blackened steel, aged copper, warm task lighting.

### Canvas & Surfaces

| Token | Value | Use |
|-------|-------|-----|
| `--color-canvas` | `#0E0D0C` | Page background — near-black with warm undertone |
| `--color-surface` | `#171614` | Base surface — cards, panels |
| `--color-surface-raised` | `#1F1D1A` | Elevated cards — modal content, form cards |
| `--color-surface-hover` | `#252320` | Hover state on interactive surfaces |
| `--color-inset` | `#141312` | Input fields — "inset" reads as recessed |

### Text

| Token | Value | Use |
|-------|-------|-----|
| `--color-text-primary` | `#F2EDE4` | Warm white — primary content |
| `--color-text-secondary` | `#A89F92` | Parchment — labels, secondary info |
| `--color-text-tertiary` | `#6B6560` | Dim — hints, timestamps, station labels |
| `--color-text-muted` | `#423E3A` | Near-invisible — disabled, placeholder |

### Copper Accent

| Token | Value | Use |
|-------|-------|-----|
| `--color-copper` | `#C8864A` | Aged copper — primary action, active states, focus |
| `--color-copper-hover` | `#D99A5A` | Brightened copper on hover |
| `--color-copper-subtle` | `#2A1E12` | Deep copper wash — subtle highlight backgrounds |
| `--color-copper-glow` | `rgba(200,134,74,0.15)` | Ambient glow for focus rings, card glows |

### Status

| Token | Value | Source | Maps to |
|-------|-------|--------|---------|
| `--color-success` | `#6AAB6E` | Herb green — fresh produce | `sysco_catalog` — exact match |
| `--color-success-subtle` | `#0F2410` | Deep green wash | Success backgrounds |
| `--color-success-text` | `#8FCC93` | Lighter green for text on dark | Success labels |
| `--color-warning` | `#C4933A` | Warm amber — golden onion skin | `estimated` — approximate match |
| `--color-warning-subtle` | `#261A08` | Deep amber wash | Warning backgrounds |
| `--color-warning-text` | `#D4A84E` | Lighter amber for text on dark | Warning labels |
| `--color-error` | `#B55A5A` | Muted brick red | `not_available` — 86'd |
| `--color-error-subtle` | `#250E0E` | Deep red wash | Error backgrounds |
| `--color-error-text` | `#CC7A7A` | Lighter red for text on dark | Error labels |

### Borders

| Token | Value | Use |
|-------|-------|-----|
| `--color-border-subtle` | `#252220` | Card edges, dividers — whisper-quiet |
| `--color-border-default` | `#332F2B` | Input borders, visible separation |
| `--color-border-strong` | `#4A4540` | Emphasis, strong dividers |
| `--color-border-accent` | `rgba(200,134,74,0.35)` | Copper accent borders on focus/active |

## Depth Strategy: Dark Shadows

Heavy, warm shadows — surfaces lifting out of a dark void. No diffuse light-mode softness here.

| Token | Value | Use |
|-------|--------|-----|
| `--shadow-sm` | `0 1px 3px rgba(0,0,0,0.4), 0 1px 2px rgba(0,0,0,0.3)` | Subtle lift — inline cards, badges |
| `--shadow-md` | `0 4px 16px rgba(0,0,0,0.5), 0 2px 6px rgba(0,0,0,0.35)` | Elevated cards, dropdowns |
| `--shadow-lg` | `0 12px 40px rgba(0,0,0,0.65), 0 4px 12px rgba(0,0,0,0.4)` | Form cards, modals |
| `--shadow-copper` | `0 0 0 3px rgba(200,134,74,0.15)` | Focus ring — copper glow |
| `--shadow-glow` | `0 0 24px rgba(200,134,74,0.12)` | Ambient copper glow on key surfaces |

The body carries two atmospheric overlays:
- **Grain texture** — fractalNoise SVG via `body::before`, `opacity: 0.03`. Adds tactile warmth.
- **Vignette** — radial-gradient darkening edges via `body::after`, `opacity: 0.4`. Focuses the eye on the center.

## Spacing

Base unit: **4px**

| Scale | Value | Use |
|-------|-------|-----|
| micro | 4px | Icon gaps, inline spacing |
| xs | 8px | Tight component internals |
| sm | 12px | Button padding, input padding |
| md | 16px | Card padding, section gaps |
| lg | 24px | Between component groups |
| xl | 32px | Major section separation |
| 2xl | 48px | Page-level spacing |

Page containers: `max-w-[1200px] mx-auto px-8` for Kitchen and Pass; `max-w-xl` centered card for Submit.

## Typography

Three-font system: a serif display, a serif body, and a monospace data font. No sans-serif — this is a luxury kitchen, not a SaaS dashboard.

| Role | Font | Fallbacks |
|------|------|-----------|
| `--font-display` | Cormorant Garamond | Georgia, serif |
| `--font-sans` | Crimson Pro | Georgia, serif |
| `--font-mono` | DM Mono | Courier New, monospace |

Body defaults: `font-family: 'Crimson Pro'`, `font-size: 16px`, `line-height: 1.55`.

| Level | Size | Weight | Tracking | Font | Use |
|-------|------|--------|----------|------|-----|
| Display | 42px | 600 | -0.03em | Cormorant Garamond | Page titles — "Kitchen", "The Pass" |
| Section title | 32px | — | — | Cormorant Garamond | Event name in Pass summary |
| Heading | 20px | 600 | — | Crimson Pro | Card titles, item names |
| Body | 16px | 400 | — | Crimson Pro | Primary content, descriptions |
| Station label | 11px | — | 0.12em | Crimson Pro | Uppercase section headers |
| Table header | 11px | — | uppercase | Crimson Pro | Ingredient table column labels |
| Data | mono | 400 | — | DM Mono | Prices, quantities — `tabular-nums` |

## Border Radius

| Token | Value | Element |
|-------|-------|---------|
| `--radius-button` | `4px` | Buttons |
| `--radius-card` | `6px` | Cards, ticket cards |
| `--radius-input` | `4px` | Input fields |
| `--radius-modal` | `8px` | Modals |
| `--radius-badge` | `3px` | Source badges, status tags |

shadcn/ui `--radius` global override: `0.375rem` (6px), matching `--radius-card`.

## Components

### Ticket Card (core component)

The fundamental unit — represents one menu item moving through the kitchen. Used in both KitchenView and PassView.

- Background: `--color-surface-raised`
- Border: `--color-border-subtle`, 1px; `--radius-card`
- Shadow: `--shadow-md`
- **Left border accent** (3–4px) encodes status:
  - Pending / active: `--color-copper`
  - Completed: `--color-success`
  - Failed / 86'd: `--color-error`
- Content padding: `p-5` (CardContent)
- Item name as subheading (Crimson Pro, 16px+)
- Ingredient count and cost in DM Mono

### Line Item Card (PassView)

Expandable ticket card for the final quote.

- Collapsed: item name + total cost, left border by status
- Expanded: ingredient table with columns — Ingredient, Qty, Unit Cost, Source, Catalog #
- Click target: full header row `px-6 py-5`
- Table header: `px-5 py-3`, 11px uppercase tracking
- Table cells: `px-5 py-3`, DM Mono for all numbers
- AnimatePresence controls expand/collapse transition

### Counter Card (KitchenView)

Four cards across the top: TOTAL ITEMS, IN PROGRESS, COMPLETED, FAILED.

- Background: `--color-surface-raised`
- Border: `--color-border-subtle`
- Radius: `rounded-lg`
- Padding: `px-5 py-4`
- Count in display font; label in 11px uppercase tracking
- IN PROGRESS: copper accent; COMPLETED: success; FAILED: error

### Source Badge

Inline indicator for ingredient match quality. Uses shadcn Badge, customized via CSS tokens.

| Source | Background | Text | Label |
|--------|-----------|------|-------|
| `sysco_catalog` | `--color-success-subtle` | `--color-success-text` | "Catalog" |
| `estimated` | `--color-warning-subtle` | `--color-warning-text` | "Estimated" |
| `not_available` | `--color-error-subtle` | `--color-error-text` | "86'd" |
| Radius: `--radius-badge` (3px) |

### Connection Pill (KitchenView)

Fixed `bottom-6 right-6`. Shows SSE connection status — small pill with colored dot and label. Floats above all content.

### Form Fields (SubmitView)

shadcn Input / Textarea components. Background: `--color-inset`. Border: `--color-border-default`. Focus ring: `--shadow-copper`. Groups use `space-y-3` (label above input).

## Views

### 1. Submit View

A centered card on the dark canvas. Feels like writing a prep list under a single overhead light.

- Layout: `min-h-screen flex items-center justify-center`
- Card: `max-w-xl`, `bg-surface-raised`, `border-border-subtle`, `shadow-lg`
- Copper top border: `border-t-2 border-copper` — the only color accent on entry
- Internal padding: `px-12 pb-12 pt-10`
- Two fieldset sections: **EVENT DETAILS** and **MENU SPEC**, separated by a `Separator` with a copper dot
- Grid: `grid-cols-[1.5fr_1fr] gap-x-5 gap-y-6` for event detail fields
- CTA: full-width copper button, uppercase, tracked label — "Start Quote"
- Motion: `fadeUp` on card entry

### 2. Kitchen View (live progress)

Full-bleed dark canvas. The ticket rail in motion.

- Sticky header: `border-b border-border-subtle`, content `max-w-[1200px] mx-auto px-8 pt-12 pb-8`
- Page title: `font-display text-[42px] font-semibold tracking-[-0.03em]`
- 4 counter cards below the title
- Main content: `max-w-[1200px] px-8 pt-10 pb-16`, `gap-10` between stations
- Station sections: 11px uppercase label + `flex-wrap gap-4` of ticket cards
- Ticket cards enter via `ticketArrive` (slide from left); stations via staggered `fadeUp`
- AnimatePresence handles card entry/exit as SSE events update state
- Connection pill fixed bottom-right

### 3. The Pass View (quote)

Final inspection. Full-bleed canvas with a contained content column.

- Layout: `px-8 pt-12 pb-20`, content `max-w-[900px] mx-auto gap-8`
- Summary card: `bg-surface-raised shadow-lg px-8 py-8`
  - Event name: `font-display text-[32px]`
  - Date, guests, venue: `text-secondary`
  - Separator + stats row: total items (copper), catalog matches (success), estimated (warning), unavailable (error)
- Line items section: uppercase label + `flex-col gap-3`
- Export button: copper `outline` variant — secondary action, not primary
- Motion: `fadeUp` on sections; AnimatePresence on each card expand/collapse

## Animation

All complex enter/exit animations use the `motion` library (Framer Motion), not CSS transitions.

| Keyframe | Behavior | Use |
|----------|----------|-----|
| `fadeUp` | Opacity 0→1, translateY 16px→0 | Page sections, card entry |
| `revealCard` | Opacity 0→1, scale 0.96→1 | Cards scaling in |
| `ticketArrive` | Opacity 0→1, translateX -12px→0 | Ticket cards sliding in from left |
| `copperPulse` | Copper accent border opacity oscillation | Active processing indicator |
| `breathe` | Scale 1→1.015→1 | Subtle living element |
| `spinSlow` | 360° rotation | Loading spinners |

No bounce or spring. Professional kitchen — smooth, efficient movements.

## Patterns to Remember

- **Dark-only theme** — no light mode, no `.dark` block. shadcn/ui tokens are overridden globally at `:root`, never inside `.dark`.
- **Copper is the sole accent** — primary actions, active states, focus rings. Not decoration. Never applied gratuitously.
- **Left-border accent on cards = status** — copper for pending/active, success green for completed, error red for failed. Consistent in both KitchenView and PassView.
- **Source badges: 3-color system** — Catalog (success), Estimated (warning), 86'd (error). Never ad-hoc status colors.
- **All prices and quantities in DM Mono** with `tabular-nums`. Always.
- **Grain texture + vignette** on `body` via pseudo-elements — adds tactile depth without imagery.
- **shadcn/ui as the component base** — customized entirely via CSS tokens. No component-level inline style overrides.
- **motion library for enter/exit animations** — CSS transitions only for simple hover/focus state changes (150ms ease-out).
- **Kitchen vocabulary in UI labels** — "86'd" not "Unavailable", "The Pass" not "Results", "Kitchen" not "Dashboard", "Ticket" not "Item card".
