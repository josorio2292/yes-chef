import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import type { KeyboardEvent, SVGProps } from 'react'
import { motion } from 'motion/react'
import { useQuoteResult } from '../api'
import type { Quote, LineItem, Ingredient } from '../schemas'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'

/* ─── Source Badge ─────────────────────────────────────────────────────────── */

type IngredientSource = 'sysco_catalog' | 'estimated' | 'not_available'

const SOURCE_MAP: Record<
  IngredientSource,
  { label: string; className: string }
> = {
  sysco_catalog: {
    label: 'Catalog',
    className: 'bg-copper-subtle text-copper',
  },
  estimated: {
    label: 'Estimated',
    className: 'bg-warning-subtle text-warning-text',
  },
  not_available: {
    label: "86'd",
    className: 'bg-error-subtle text-error-text',
  },
}

function SourceBadge({ source }: { source: string }) {
  const config = SOURCE_MAP[source as IngredientSource] ?? {
    label: source,
    className: 'bg-surface text-text-secondary',
  }
  return (
    <Badge
      variant="outline"
      className={cn(
        'font-mono text-[11px] border-border-subtle',
        config.className
      )}
    >
      {config.label}
    </Badge>
  )
}

/* ─── Ingredient Table ─────────────────────────────────────────────────────── */

function IngredientTable({ ingredients }: { ingredients: Ingredient[] }) {
  if (!ingredients || ingredients.length === 0) {
    return (
      <p className="px-4 py-3 text-text-tertiary text-base">
        No ingredients recorded.
      </p>
    )
  }

  return (
    <table className="w-full border-collapse text-sm" aria-label="Ingredients">
      <thead className="bg-surface">
        <tr>
          <th className="px-5 py-3 text-[11px] font-medium tracking-[0.08em] uppercase text-text-tertiary text-left border-b border-border-subtle whitespace-nowrap">
            Ingredient
          </th>
          <th className="px-5 py-3 text-[11px] font-medium tracking-[0.08em] uppercase text-text-tertiary text-left border-b border-border-subtle whitespace-nowrap">
            Quantity
          </th>
          <th className="px-5 py-3 text-[11px] font-medium tracking-[0.08em] uppercase text-text-tertiary text-right border-b border-border-subtle whitespace-nowrap">
            Unit Cost
          </th>
          <th className="px-5 py-3 text-[11px] font-medium tracking-[0.08em] uppercase text-text-tertiary text-left border-b border-border-subtle whitespace-nowrap">
            Source
          </th>
          <th className="px-5 py-3 text-[11px] font-medium tracking-[0.08em] uppercase text-text-tertiary text-right border-b border-border-subtle whitespace-nowrap">
            Catalog #
          </th>
        </tr>
      </thead>
      <tbody>
        {ingredients.map((ing, idx) => (
          <tr
            key={idx}
            className="border-t border-border-subtle hover:bg-surface-hover transition-colors"
          >
            <td className="px-5 py-3 text-text-primary min-w-[120px]">
              {ing.name}
            </td>
            <td className="px-5 py-3 text-text-secondary whitespace-nowrap min-w-[80px]">
              {ing.quantity}
            </td>
            <td className="px-5 py-3 font-mono tabular-nums text-text-primary text-right whitespace-nowrap min-w-[80px]">
              {formatCurrency(ing.unit_cost)}
            </td>
            <td className="px-5 py-3 whitespace-nowrap min-w-[100px]">
              <SourceBadge source={ing.source} />
            </td>
            <td className="px-5 py-3 font-mono tabular-nums text-[11px] text-text-tertiary text-right whitespace-nowrap min-w-[100px]">
              {ing.source_item_id ?? <span className="text-text-muted">—</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/* ─── Line Item Card ───────────────────────────────────────────────────────── */

function LineItemCard({ item, idx }: { item: LineItem; idx: number }) {
  const [expanded, setExpanded] = useState(false)

  const toggleExpanded = () => setExpanded((prev) => !prev)

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      toggleExpanded()
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay: idx * 0.06 }}
    >
      <Card
        className="bg-surface-raised border-border-subtle shadow-sm overflow-hidden hover:shadow-md transition-shadow duration-200 py-0 gap-0"
        aria-expanded={expanded}
      >
        <div
          className="flex items-center justify-between px-6 py-5 cursor-pointer select-none gap-4 hover:bg-surface-hover transition-colors duration-150"
          onClick={toggleExpanded}
          onKeyDown={handleKeyDown}
          role="button"
          tabIndex={0}
          aria-label={`${item.item_name} — ${expanded ? 'collapse' : 'expand'}`}
        >
          <div className="flex items-center gap-2.5 flex-1 min-w-0">
            <span className="text-[17px] font-semibold text-text-primary whitespace-nowrap overflow-hidden text-ellipsis">
              {item.item_name}
            </span>
            {item.category && (
              <Badge
                variant="outline"
                className="shrink-0 bg-surface border-border-subtle text-text-secondary text-[11px] capitalize"
              >
                {item.category}
              </Badge>
            )}
          </div>

          <div className="flex items-center gap-4 shrink-0">
            <span className="font-mono tabular-nums text-[15px] text-text-primary text-right whitespace-nowrap">
              {formatCurrency(item.ingredient_cost_per_unit)}
            </span>
            <ChevronDownIcon
              className={cn(
                'w-4 h-4 text-text-tertiary shrink-0 transition-transform duration-200 ease-out',
                expanded && 'rotate-180'
              )}
              aria-hidden="true"
            />
          </div>
        </div>

        <div
          className={`overflow-hidden transition-all duration-[250ms] ease-out ${expanded ? 'max-h-[600px]' : 'max-h-0'}`}
          aria-hidden={!expanded}
        >
          <div className="border-t border-border-subtle">
            <IngredientTable ingredients={item.ingredients} />
          </div>
        </div>
      </Card>
    </motion.div>
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
  return lineItems.reduce(
    (sum, item) => sum + (item.ingredient_cost_per_unit ?? 0),
    0
  )
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
  const blob = new Blob([JSON.stringify(quote, null, 2)], {
    type: 'application/json',
  })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `quote-${quote.quote_id ?? 'export'}.json`
  a.click()
  URL.revokeObjectURL(url)
}

/* ─── Pass View ────────────────────────────────────────────────────────────── */

export default function PassView() {
  const { quoteId } = useParams<{ quoteId: string }>()
  const {
    data: quote,
    isLoading,
    error,
  } = useQuoteResult(quoteId ?? '', !!quoteId)

  /* ── Loading ── */
  if (isLoading) {
    return (
      <div className="min-h-screen bg-canvas px-8 pt-12 pb-20">
        <div className="max-w-[900px] mx-auto">
          <p className="text-center py-16 text-text-tertiary text-base">
            Loading quote…
          </p>
        </div>
      </div>
    )
  }

  /* ── Error ── */
  if (error) {
    return (
      <div className="min-h-screen bg-canvas px-8 pt-12 pb-20">
        <div className="max-w-[900px] mx-auto">
          <p className="text-center py-16 text-error text-base">
            {error instanceof Error ? error.message : 'Failed to load quote.'}
          </p>
        </div>
      </div>
    )
  }

  /* ── Empty ── */
  if (!quote) {
    return (
      <div className="min-h-screen bg-canvas px-8 pt-12 pb-20">
        <div className="max-w-[900px] mx-auto">
          <p className="text-center py-16 text-text-tertiary text-base">
            No quote available.
          </p>
        </div>
      </div>
    )
  }

  const lineItems = quote.line_items ?? []
  const total = computeTotal(lineItems)

  return (
    <main className="min-h-screen bg-canvas px-8 pt-12 pb-20">
      <motion.div
        className="max-w-[900px] mx-auto flex flex-col gap-8"
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
      >
        {/* ── Back navigation ── */}
        <Link
          to="/"
          className="inline-block text-[13px] text-text-tertiary hover:text-copper transition-colors duration-150 tracking-wide"
        >
          ← All Quotes
        </Link>

        {/* ── Summary Header ── */}
        <Card
          className="bg-surface-raised border-border-subtle shadow-lg px-8 py-8 gap-0"
          aria-label="Quote summary"
        >
          <CardContent className="px-0 flex flex-col gap-3">
            <h1 className="font-display text-[36px] font-semibold tracking-[-0.02em] text-text-primary italic leading-tight m-0">
              {quote.event}
            </h1>

            <div className="flex items-center gap-3 text-base text-text-secondary flex-wrap">
              {quote.date && (
                <>
                  <span>{formatDate(quote.date)}</span>
                  {quote.venue && (
                    <span className="text-text-muted select-none"> — </span>
                  )}
                </>
              )}
              {quote.venue && <span>{quote.venue}</span>}
            </div>

            <Separator className="mt-4 bg-border-subtle" />

            <div className="flex items-center justify-between pt-5 flex-wrap gap-4">
              <p className="text-base text-text-secondary">
                <strong className="font-semibold text-text-primary">
                  {lineItems.length}
                </strong>{' '}
                {lineItems.length === 1 ? 'menu item' : 'menu items'}
              </p>

              <div className="text-right">
                <span className="font-mono text-[36px] font-medium tabular-nums text-text-primary leading-none">
                  {formatCurrency(total)}
                </span>
                <span className="block text-[11px] font-medium tracking-[0.12em] uppercase text-text-tertiary mt-1">
                  Total Ingredient Cost
                </span>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* ── Line Items ── */}
        <section aria-label="Line items">
          {lineItems.length === 0 ? (
            <p className="text-center py-16 text-text-tertiary text-base">
              No line items in this quote.
            </p>
          ) : (
            <div className="flex flex-col gap-3">
              <p className="text-[11px] font-medium tracking-[0.12em] uppercase text-text-tertiary mb-4">
                Line Items
              </p>
              {lineItems.map((item, idx) => (
                <LineItemCard
                  key={item.item_name ?? idx}
                  item={item}
                  idx={idx}
                />
              ))}
            </div>
          )}
        </section>

        {/* ── Export ── */}
        <div className="flex justify-center pt-4">
          <Button
            variant="ghost"
            className="inline-flex items-center gap-2 text-[13px] text-text-tertiary hover:text-text-primary"
            onClick={() => exportQuote(quote)}
            aria-label="Export quote as JSON file"
          >
            <DownloadIcon />
            Export JSON
          </Button>
        </div>
      </motion.div>
    </main>
  )
}
