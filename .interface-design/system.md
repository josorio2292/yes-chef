# Yes Chef — Design System

> Interface design system for the Yes Chef catering estimation tool.
> Reference: `interface-design` skill.

---

## Intent

- **Who:** Catering company owners and event planners. Practical, time-pressured. Not developers.
- **Task:** Submit a menu → watch ingredients resolve in real-time → get a priced quote for client proposals.
- **Feel:** Like a well-run professional kitchen. Confident, organized, warm. Mise en place — everything in its place. Not cold/corporate, not playful/casual. Calm authority.

## Direction: "The Pass"

A professional kitchen command center. Three views that mirror the kitchen workflow:

1. **Submit** — menu input. Clean, focused, like writing a prep list.
2. **Kitchen** — live progress. Ticket rail showing items moving through stations.
3. **The Pass** — final quote review. Polished, inspected, ready to send to client.

---

## Domain

| Concept | UI mapping |
|---------|-----------|
| Mise en place | Overall layout — organized, nothing extraneous |
| Ticket rail | Progress view — items as cards moving through stations |
| The pass | Quote view — final inspection before it goes out |
| Prep station | Decompose step — raw menu → ingredient list |
| 86'd | `not_available` status — kitchen slang for "out of it" |
| Plating | Quote output — the final presentation |

## Palette

Derived from a professional kitchen — not applied to it.

### Foundation

| Token | Value | Source |
|-------|-------|--------|
| `--bg-canvas` | `#FAF8F5` | Butcher paper / marble countertop |
| `--bg-surface` | `#F2EFEB` | Parchment — cards rest on this |
| `--bg-surface-raised` | `#FFFFFF` | Ticket cards — slight lift above surface |
| `--bg-inset` | `#ECEAE6` | Input fields — "inset" signals "type here" |

### Text

| Token | Value | Use |
|-------|-------|-----|
| `--text-primary` | `#2C2C2C` | Slate/charcoal — primary content |
| `--text-secondary` | `#5C5C5C` | Cast iron grey — labels, secondary info |
| `--text-tertiary` | `#8C8C8C` | Muted — hints, timestamps |
| `--text-muted` | `#ACACAC` | Disabled, placeholder |

### Accent

| Token | Value | Source |
|-------|-------|--------|
| `--accent` | `#C17F4E` | Copper — pots, fixtures. Used for CTAs, active states |
| `--accent-hover` | `#A96B3E` | Darker copper on hover |
| `--accent-subtle` | `#F5EDE5` | Copper wash — subtle highlight backgrounds |

### Status

| Token | Value | Source | Maps to |
|-------|-------|--------|---------|
| `--status-success` | `#5B8A5E` | Herb green — fresh produce | `sysco_catalog` — exact match |
| `--status-success-subtle` | `#EFF5EF` | Light herb wash | Success backgrounds |
| `--status-warning` | `#C49A3C` | Warm amber — golden onion skin | `estimated` — approximate match |
| `--status-warning-subtle` | `#FBF5E8` | Light amber wash | Warning backgrounds |
| `--status-error` | `#B05A5A` | Muted brick red — not harsh | `not_available` — 86'd |
| `--status-error-subtle` | `#F9F0F0` | Light red wash | Error backgrounds |
| `--status-neutral` | `#8C8C8C` | Cast iron grey | Pending, in-progress |

### Borders

| Token | Value | Use |
|-------|-------|-----|
| `--border-subtle` | `#E5E2DE` | Card edges, dividers — whisper-quiet |
| `--border-default` | `#D4D0CC` | Input borders, more visible separation |
| `--border-strong` | `#B0ADA8` | Focus rings, emphasis |

## Depth Strategy: Subtle Shadows

Soft lift — approachable, like recipe cards resting on a prep surface. One strategy throughout.

| Level | Shadow | Use |
|-------|--------|-----|
| 0 | none | Flat surfaces, inset inputs |
| 1 | `0 1px 2px rgba(0,0,0,0.04)` | Cards at rest — ticket cards on the rail |
| 2 | `0 2px 8px rgba(0,0,0,0.06)` | Hovered cards, dropdowns |
| 3 | `0 4px 16px rgba(0,0,0,0.08)` | Modals, popovers |

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

## Typography

Slightly rounded sans-serif — approachable but professional. Not a cold tech font.

**Font:** Inter (widely available, slightly rounded terminals, excellent at small sizes for data-dense views).

| Level | Size | Weight | Tracking | Use |
|-------|------|--------|----------|-----|
| Display | 28px | 600 | -0.02em | Page titles — "The Pass", "Kitchen" |
| Heading | 20px | 600 | -0.01em | Section headers — "Appetizers", "Main Plates" |
| Subheading | 16px | 500 | 0 | Card titles — item names |
| Body | 14px | 400 | 0 | Primary content, descriptions |
| Label | 12px | 500 | 0.01em | Field labels, category tags |
| Data | 14px / mono | 400 | 0 | Prices, quantities — `tabular-nums`, monospace for alignment |
| Caption | 11px | 400 | 0.02em | Timestamps, metadata |

## Border Radius

| Element | Radius |
|---------|--------|
| Buttons | 6px |
| Cards / tickets | 8px |
| Inputs | 6px |
| Modals | 12px |
| Tags / badges | 4px |

## Components

### Ticket Card (core component)

The fundamental unit — represents one menu item moving through the kitchen.

- Surface: `--bg-surface-raised` with shadow level 1
- Border: `--border-subtle`, 1px
- Radius: 8px
- Padding: 16px
- **Header:** Item name (subheading weight) + category tag (label, `--border-subtle` bg)
- **Body:** Ingredient list or status depending on view
- **Footer:** Cost or progress indicator
- **States:**
  - Pending: neutral border, muted text
  - Processing: left border accent (`--accent`, 3px)
  - Completed: left border success (`--status-success`, 3px)
  - Failed: left border error (`--status-error`, 3px)

### Source Badge

Inline indicator for ingredient match quality.

| Source | Background | Text | Label |
|--------|-----------|------|-------|
| `sysco_catalog` | `--status-success-subtle` | `--status-success` | "Catalog" |
| `estimated` | `--status-warning-subtle` | `--status-warning` | "Estimated" |
| `not_available` | `--status-error-subtle` | `--status-error` | "86'd" |

### Station Headers

In the Kitchen view, stations label the ticket rail columns:

- **Prep** (decomposing) — icon: knife
- **Match** (resolving) — icon: search
- **Done** (completed) — icon: check
- **86'd** (failed) — icon: x

Typography: Label size, uppercase, `--text-tertiary`. Subtle, not shouty.

## Views

### 1. Submit View

Clean form. Feels like writing a prep list.

- Textarea or JSON upload for menu spec
- Event details (name, date, venue, guest count) as simple fields
- Single copper CTA: "Start Quote"
- Minimal — no sidebar, no nav complexity

### 2. Kitchen View (live progress)

The ticket rail. Items flow through stations.

- **Layout:** Columns for each station (Prep → Match → Done), or a single list with status indicators if column layout is too complex for prototype
- **Ticket cards** move between stations as SSE events arrive
- **Live counters** at top: total items, in progress, completed, failed
- Processing items show the copper left-border accent
- Completed items show ingredient count + cost summary

### 3. The Pass View (quote)

Final inspection. Polished output.

- **Summary header:** Event name, date, total items, total cost
- **Line items:** Expandable cards — collapsed shows item name + cost, expanded shows full ingredient table
- **Ingredient table:** Name, quantity, unit cost, source badge, catalog item number
- **Export:** Copy JSON or download — single action
- Items with `not_available` ingredients are flagged but not hidden

## Animation

| Type | Duration | Easing |
|------|----------|--------|
| Micro (hover, focus) | 150ms | ease-out |
| Card transitions | 200ms | ease-out |
| Station movement | 250ms | ease-in-out |

No spring/bounce. Professional kitchen — smooth, efficient movements.

## Patterns to Remember

- **Left-border accent** on cards = processing state. Consistent everywhere.
- **Source badges** always use the same three-color system. Never ad-hoc status colors.
- **Copper is precious** — only for primary actions and active processing. Not decoration.
- **Data alignment** — prices and quantities use `tabular-nums` monospace. Always right-aligned.
- **Kitchen vocabulary** in UI labels where natural: "86'd" not "unavailable", "The Pass" not "Results".
