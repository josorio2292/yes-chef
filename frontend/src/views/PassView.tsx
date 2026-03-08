import { useState, useEffect } from 'react';
import type { KeyboardEvent, SVGProps } from 'react';
import './PassView.css';

/* ─── Types ────────────────────────────────────────────────────────────────── */

type IngredientSource = 'sysco_catalog' | 'estimated' | 'not_available';

interface Ingredient {
  name: string;
  quantity: string;
  unit_cost: number;
  source: IngredientSource;
  sysco_item_number?: string | null;
}

interface LineItem {
  item_name: string;
  category?: string;
  ingredients: Ingredient[];
  ingredient_cost_per_unit: number;
}

interface Quote {
  quote_id: string;
  event: string;
  date?: string;
  venue?: string;
  generated_at?: string;
  line_items: LineItem[];
}

/* ─── Source Badge ─────────────────────────────────────────────────────────── */

const SOURCE_MAP: Record<string, { label: string; cls: string }> = {
  sysco_catalog: { label: 'Catalog', cls: 'source-badge--catalog' },
  estimated: { label: 'Estimated', cls: 'source-badge--estimated' },
  not_available: { label: "86'd", cls: 'source-badge--eightysixed' },
};

function SourceBadge({ source }: { source: string }) {
  const { label, cls } = SOURCE_MAP[source] ?? { label: source, cls: '' };
  return <span className={`source-badge ${cls}`}>{label}</span>;
}

/* ─── Ingredient Table ─────────────────────────────────────────────────────── */

function IngredientTable({ ingredients }: { ingredients: Ingredient[] }) {
  if (!ingredients || ingredients.length === 0) {
    return (
      <p style={{ padding: '12px 16px', color: 'var(--text-tertiary)', fontSize: 14, margin: 0 }}>
        No ingredients recorded.
      </p>
    );
  }

  return (
    <table className="ingredient-table" aria-label="Ingredients">
      <thead className="ingredient-table__head">
        <tr>
          <th className="col-name">Ingredient</th>
          <th className="col-qty">Quantity</th>
          <th className="col-cost">Unit Cost</th>
          <th className="col-source">Source</th>
          <th className="col-catalog">Catalog #</th>
        </tr>
      </thead>
      <tbody className="ingredient-table__body">
        {ingredients.map((ing, idx) => (
          <tr key={idx}>
            <td className="col-name">{ing.name}</td>
            <td className="col-qty">{ing.quantity}</td>
            <td className="col-cost">{formatCurrency(ing.unit_cost)}</td>
            <td className="col-source">
              <SourceBadge source={ing.source} />
            </td>
            <td className="col-catalog">
              {ing.sysco_item_number ?? (
                <span style={{ color: 'var(--text-muted)' }}>—</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* ─── Line Item Card ───────────────────────────────────────────────────────── */

function LineItemCard({ item }: { item: LineItem }) {
  const [expanded, setExpanded] = useState(false);

  const toggleExpanded = () => setExpanded((prev) => !prev);

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      toggleExpanded();
    }
  };

  return (
    <article
      className={`line-item${expanded ? ' line-item--expanded' : ''}`}
      aria-expanded={expanded}
    >
      <div
        className="line-item__header"
        onClick={toggleExpanded}
        onKeyDown={handleKeyDown}
        role="button"
        tabIndex={0}
        aria-label={`${item.item_name} — ${expanded ? 'collapse' : 'expand'}`}
      >
        <div className="line-item__header-left">
          <span className="line-item__name">{item.item_name}</span>
          {item.category && (
            <span className="line-item__category">{item.category}</span>
          )}
        </div>

        <div className="line-item__header-right">
          <span className="line-item__cost">
            {formatCurrency(item.ingredient_cost_per_unit)}
          </span>
          <ChevronDownIcon className="line-item__chevron" aria-hidden="true" />
        </div>
      </div>

      <div className="line-item__body" aria-hidden={!expanded}>
        <div className="line-item__body-inner">
          <IngredientTable ingredients={item.ingredients} />
        </div>
      </div>
    </article>
  );
}

/* ─── Helpers ──────────────────────────────────────────────────────────────── */

function formatCurrency(value: number | null | undefined): string {
  if (value == null) return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function formatDate(dateStr: string): string {
  if (!dateStr) return '';
  const d = new Date(dateStr + 'T00:00:00');
  return d.toLocaleDateString('en-US', {
    weekday: 'short',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });
}

function computeTotal(lineItems: LineItem[]): number {
  return lineItems.reduce((sum, item) => sum + (item.ingredient_cost_per_unit ?? 0), 0);
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
  );
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
  );
}

/* ─── Export ───────────────────────────────────────────────────────────────── */

function exportQuote(quote: Quote): void {
  const blob = new Blob([JSON.stringify(quote, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `quote-${quote.quote_id ?? 'export'}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

/* ─── Pass View ────────────────────────────────────────────────────────────── */

interface PassViewProps {
  /** Job ID — used to fetch from GET /jobs/{jobId}/quote */
  jobId?: string;
  /** Pass a pre-fetched quote directly (testing / integration) */
  quote?: Quote;
}

export default function PassView({ jobId, quote: quoteProp }: PassViewProps) {
  const [quote, setQuote] = useState<Quote | null>(quoteProp ?? null);
  const [loading, setLoading] = useState(!quoteProp);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (quoteProp) {
      setQuote(quoteProp);
      setLoading(false);
      return;
    }

    if (!jobId) {
      setLoading(false);
      setError('No job ID provided.');
      return;
    }

    let cancelled = false;

    async function fetchQuote() {
      try {
        const res = await fetch(`/jobs/${jobId}/quote`);
        if (!res.ok) {
          throw new Error(`Server returned ${res.status} ${res.statusText}`);
        }
        const data: Quote = await res.json();
        if (!cancelled) {
          setQuote(data);
          setLoading(false);
        }
      } catch (err: unknown) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load quote.');
          setLoading(false);
        }
      }
    }

    fetchQuote();

    return () => {
      cancelled = true;
    };
  }, [jobId, quoteProp]);

  /* ── Loading ── */
  if (loading) {
    return (
      <div className="pass-view">
        <div className="pass-view__inner">
          <p className="pass-view__loading">Loading quote…</p>
        </div>
      </div>
    );
  }

  /* ── Error ── */
  if (error) {
    return (
      <div className="pass-view">
        <div className="pass-view__inner">
          <p className="pass-view__error">{error}</p>
        </div>
      </div>
    );
  }

  /* ── Empty ── */
  if (!quote) {
    return (
      <div className="pass-view">
        <div className="pass-view__inner">
          <p className="pass-view__empty">No quote available.</p>
        </div>
      </div>
    );
  }

  const lineItems = quote.line_items ?? [];
  const total = computeTotal(lineItems);

  return (
    <main className="pass-view">
      <div className="pass-view__inner">

        {/* ── Summary Header ── */}
        <section className="pass-summary" aria-label="Quote summary">
          <h1 className="pass-summary__event">{quote.event}</h1>

          <div className="pass-summary__meta">
            {quote.date && (
              <>
                <span>{formatDate(quote.date)}</span>
                {quote.venue && <span className="pass-summary__meta-sep">·</span>}
              </>
            )}
            {quote.venue && <span>{quote.venue}</span>}
          </div>

          <div className="pass-summary__stats">
            <p className="pass-summary__count">
              <strong>{lineItems.length}</strong>{' '}
              {lineItems.length === 1 ? 'menu item' : 'menu items'}
            </p>

            <div className="pass-summary__total">
              <span className="pass-summary__total-label">Total Cost</span>
              <span className="pass-summary__total-value">{formatCurrency(total)}</span>
            </div>
          </div>
        </section>

        {/* ── Line Items ── */}
        <section aria-label="Line items">
          {lineItems.length === 0 ? (
            <p className="pass-view__empty">No line items in this quote.</p>
          ) : (
            <div className="pass-items">
              <p className="pass-items__heading">Line Items</p>
              {lineItems.map((item, idx) => (
                <LineItemCard key={item.item_name ?? idx} item={item} />
              ))}
            </div>
          )}
        </section>

        {/* ── Export ── */}
        <div className="pass-export">
          <button
            type="button"
            className="pass-export__btn"
            onClick={() => exportQuote(quote)}
            aria-label="Export quote as JSON file"
          >
            <DownloadIcon />
            Export JSON
          </button>
        </div>

      </div>
    </main>
  );
}
