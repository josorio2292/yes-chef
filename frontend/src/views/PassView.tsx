import { useState } from 'react'
import { useParams } from 'react-router-dom'
import type { KeyboardEvent, SVGProps } from 'react'
import { useQuote } from '../api'
import type { Quote, LineItem, Ingredient } from '../schemas'

/* ─── Source Badge ─────────────────────────────────────────────────────────── */

type IngredientSource = 'sysco_catalog' | 'estimated' | 'not_available'

const SOURCE_MAP: Record<IngredientSource, { label: string; className: string }> = {
  sysco_catalog: {
    label: 'Catalog',
    className: 'bg-success-subtle text-success',
  },
  estimated: {
    label: 'Estimated',
    className: 'bg-warning-subtle text-warning',
  },
  not_available: {
    label: "86'd",
    className: 'bg-error-subtle text-error',
  },
}

function SourceBadge({ source }: { source: string }) {
  const config = SOURCE_MAP[source as IngredientSource] ?? { label: source, className: 'bg-surface text-text-secondary' }
  return (
    <span className={`inline-block text-[11px] font-medium px-2 py-0.5 rounded-[4px] whitespace-nowrap ${config.className}`}>
      {config.label}
    </span>
  )
}

/* ─── Ingredient Table ─────────────────────────────────────────────────────── */

function IngredientTable({ ingredients }: { ingredients: Ingredient[] }) {
  if (!ingredients || ingredients.length === 0) {
    return (
      <p className="px-4 py-3 text-text-tertiary text-sm">
        No ingredients recorded.
      </p>
    )
  }

  return (
    <table className="w-full border-collapse text-sm" aria-label="Ingredients">
      <thead className="bg-surface">
        <tr>
          <th className="px-4 py-2 text-xs font-medium tracking-wide uppercase text-text-secondary text-left border-b border-border-subtle whitespace-nowrap">
            Ingredient
          </th>
          <th className="px-4 py-2 text-xs font-medium tracking-wide uppercase text-text-secondary text-left border-b border-border-subtle whitespace-nowrap">
            Quantity
          </th>
          <th className="px-4 py-2 text-xs font-medium tracking-wide uppercase text-text-secondary text-right border-b border-border-subtle whitespace-nowrap">
            Unit Cost
          </th>
          <th className="px-4 py-2 text-xs font-medium tracking-wide uppercase text-text-secondary text-left border-b border-border-subtle whitespace-nowrap">
            Source
          </th>
          <th className="px-4 py-2 text-xs font-medium tracking-wide uppercase text-text-secondary text-right border-b border-border-subtle whitespace-nowrap">
            Catalog #
          </th>
        </tr>
      </thead>
      <tbody>
        {ingredients.map((ing, idx) => (
          <tr key={idx} className={`border-t border-border-subtle first:border-t-0 transition-colors duration-150 hover:bg-surface ${idx % 2 === 0 ? 'bg-surface/50' : ''}`}>
            <td className="px-4 py-2.5 text-text-primary min-w-[120px]">
              {ing.name}
            </td>
            <td className="px-4 py-2.5 text-text-secondary whitespace-nowrap min-w-[80px]">
              {ing.quantity}
            </td>
            <td className="px-4 py-2.5 font-mono tabular-nums text-text-primary text-right whitespace-nowrap min-w-[80px]">
              {formatCurrency(ing.unit_cost)}
            </td>
            <td className="px-4 py-2.5 whitespace-nowrap min-w-[100px]">
              <SourceBadge source={ing.source} />
            </td>
            <td className="px-4 py-2.5 font-mono tabular-nums text-[11px] text-text-tertiary text-right whitespace-nowrap min-w-[100px]">
              {ing.source_item_id ?? (
                <span className="text-text-muted">—</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/* ─── Line Item Card ───────────────────────────────────────────────────────── */

function LineItemCard({ item }: { item: LineItem }) {
  const [expanded, setExpanded] = useState(false)

  const toggleExpanded = () => setExpanded((prev) => !prev)

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      toggleExpanded()
    }
  }

  return (
    <article
      className="bg-surface-raised border border-border-subtle rounded-card shadow-sm overflow-hidden transition-shadow duration-200 hover:shadow-md"
      aria-expanded={expanded}
    >
      <div
        className="flex items-center justify-between px-4 py-3.5 cursor-pointer select-none gap-3 transition-colors duration-150 hover:bg-surface"
        onClick={toggleExpanded}
        onKeyDown={handleKeyDown}
        role="button"
        tabIndex={0}
        aria-label={`${item.item_name} — ${expanded ? 'collapse' : 'expand'}`}
      >
        <div className="flex items-center gap-2.5 flex-1 min-w-0">
          <span className="text-base font-medium text-text-primary whitespace-nowrap overflow-hidden text-ellipsis">
            {item.item_name}
          </span>
          {item.category && (
            <span className="shrink-0 inline-block text-xs font-medium tracking-wide text-text-secondary bg-inset border border-border-subtle rounded-badge px-2 py-0.5 capitalize whitespace-nowrap">
              {item.category}
            </span>
          )}
        </div>

        <div className="flex items-center gap-4 shrink-0">
          <span className="font-mono tabular-nums text-sm text-text-primary text-right whitespace-nowrap">
            {formatCurrency(item.ingredient_cost_per_unit)}
          </span>
          <ChevronDownIcon
            className={`w-4 h-4 text-text-tertiary shrink-0 transition-transform duration-200 ease-out ${expanded ? 'rotate-180' : ''}`}
            aria-hidden="true"
          />
        </div>
      </div>

      <div
        className={`overflow-hidden transition-all duration-200 ease-out ${expanded ? 'max-h-[600px]' : 'max-h-0'}`}
        aria-hidden={!expanded}
      >
        <div className="border-t border-border-subtle">
          <IngredientTable ingredients={item.ingredients} />
        </div>
      </div>
    </article>
  )
}

/* ─── Helpers ──────────────────────────────────────────────────────────────── */

function formatCurrency(value: number | null | undefined): string {
  if (value == null) return '—'
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value)
}

function formatDate(dateStr: string): string {
  if (!dateStr) return ''
  const d = new Date(dateStr + 'T00:00:00')
  return d.toLocaleDateString('en-US', {
    weekday: 'short',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

function computeTotal(lineItems: LineItem[]): number {
  return lineItems.reduce((sum, item) => sum + (item.ingredient_cost_per_unit ?? 0), 0)
}

/* ─── SVG Icons ────────────────────────────────────────────────────────────── */

function ChevronDownIcon({ className, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      {...props}
    >
      <polyline points="6 9 12 15 18 9" />
    </svg>
  )
}

function DownloadIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </svg>
  )
}

/* ─── Export ───────────────────────────────────────────────────────────────── */

function exportQuote(quote: Quote): void {
  const blob = new Blob([JSON.stringify(quote, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `quote-${quote.quote_id ?? 'export'}.json`
  a.click()
  URL.revokeObjectURL(url)
}

/* ─── Pass View ────────────────────────────────────────────────────────────── */

export default function PassView() {
  const { jobId } = useParams<{ jobId: string }>()
  const { data: quote, isLoading, error } = useQuote(jobId ?? '', !!jobId)

  /* ── Loading ── */
  if (isLoading) {
    return (
      <div className="min-h-screen bg-canvas px-6 py-8">
        <div className="max-w-[900px] mx-auto">
          <p className="text-center py-16 text-text-tertiary text-sm">Loading quote…</p>
        </div>
      </div>
    )
  }

  /* ── Error ── */
  if (error) {
    return (
      <div className="min-h-screen bg-canvas px-6 py-8">
        <div className="max-w-[900px] mx-auto">
          <p className="text-center py-16 text-error text-sm">
            {error instanceof Error ? error.message : 'Failed to load quote.'}
          </p>
        </div>
      </div>
    )
  }

  /* ── Empty ── */
  if (!quote) {
    return (
      <div className="min-h-screen bg-canvas px-6 py-8">
        <div className="max-w-[900px] mx-auto">
          <p className="text-center py-16 text-text-tertiary text-sm">No quote available.</p>
        </div>
      </div>
    )
  }

  const lineItems = quote.line_items ?? []
  const total = computeTotal(lineItems)

  return (
    <main className="min-h-screen bg-canvas px-6 pt-8 pb-12">
      <div className="max-w-[900px] mx-auto flex flex-col gap-6">

        {/* ── Summary Header ── */}
        <section
          className="bg-surface-raised border border-border-subtle rounded-card shadow-sm p-6 flex flex-col gap-2"
          aria-label="Quote summary"
        >
          <h1 className="text-[28px] font-semibold tracking-[-0.02em] text-text-primary leading-tight m-0">
            {quote.event}
          </h1>

          <div className="flex items-center gap-3 text-sm text-text-secondary flex-wrap">
            {quote.date && (
              <>
                <span>{formatDate(quote.date)}</span>
                {quote.venue && <span className="text-text-muted select-none">·</span>}
              </>
            )}
            {quote.venue && <span>{quote.venue}</span>}
          </div>

          <div className="flex items-center justify-between mt-3 pt-4 border-t border-border-subtle flex-wrap gap-3">
            <p className="text-sm text-text-secondary">
              <strong className="font-semibold text-text-primary">{lineItems.length}</strong>{' '}
              {lineItems.length === 1 ? 'menu item' : 'menu items'}
            </p>

            <div className="text-right">
              <span className="block text-[11px] font-semibold tracking-[0.08em] uppercase text-text-tertiary mb-1 font-sans">
                Total Cost
              </span>
              <span className="text-[28px] font-mono font-semibold tabular-nums text-text-primary leading-none">
                {formatCurrency(total)}
              </span>
            </div>
          </div>
        </section>

        {/* ── Line Items ── */}
        <section aria-label="Line items">
          {lineItems.length === 0 ? (
            <p className="text-center py-16 text-text-tertiary text-sm">
              No line items in this quote.
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              <p className="text-[11px] font-semibold tracking-[0.08em] uppercase text-text-tertiary mb-2">
                Line Items
              </p>
              {lineItems.map((item, idx) => (
                <LineItemCard key={item.item_name ?? idx} item={item} />
              ))}
            </div>
          )}
        </section>

        {/* ── Export ── */}
        <div className="flex justify-center pt-2">
          <button
            type="button"
            className="inline-flex items-center gap-2 font-sans text-sm font-medium text-text-secondary bg-surface border border-border-default rounded-card px-4 py-2.5 cursor-pointer transition-all duration-150 hover:border-border-strong hover:text-text-primary active:bg-inset select-none"
            onClick={() => exportQuote(quote)}
            aria-label="Export quote as JSON file"
          >
            <DownloadIcon />
            Export JSON
          </button>
        </div>

      </div>
    </main>
  )
}
