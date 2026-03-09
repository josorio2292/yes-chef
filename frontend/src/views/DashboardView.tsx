import { useNavigate } from 'react-router-dom'
import { motion } from 'motion/react'
import { useQuotes } from '@/api'
import type { QuoteSummary } from '@/schemas'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatRelativeDate(iso: string): string {
  const date = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))

  if (diffDays === 0) return 'Today'
  if (diffDays === 1) return 'Yesterday'
  if (diffDays < 7) return `${diffDays} days ago`
  if (diffDays < 30) return `${Math.floor(diffDays / 7)} week${Math.floor(diffDays / 7) > 1 ? 's' : ''} ago`
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function formatEventDate(iso: string): string {
  const date = new Date(iso)
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function quoteDestination(quote: QuoteSummary): string {
  if (quote.status === 'pending' || quote.status === 'processing') {
    return `/kitchen/${quote.quote_id}`
  }
  return `/pass/${quote.quote_id}`
}

// ── Status Badge ──────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const configs: Record<string, { label: string; className: string }> = {
    pending: {
      label: 'Pending',
      className: 'bg-copper-subtle text-copper border-transparent',
    },
    processing: {
      label: 'Processing',
      className: 'bg-copper-subtle text-copper border-transparent',
    },
    completed: {
      label: 'Completed',
      className: 'bg-success-subtle text-success-text border-transparent',
    },
    completed_with_errors: {
      label: 'Completed w/ errors',
      className: 'bg-warning-subtle text-warning-text border-transparent',
    },
    failed: {
      label: 'Failed',
      className: 'bg-error-subtle text-error-text border-transparent',
    },
  }

  const config = configs[status] ?? {
    label: status,
    className: 'bg-surface text-text-secondary border-border-subtle',
  }

  return (
    <Badge className={`text-[11px] font-medium tracking-[0.04em] ${config.className}`}>
      {config.label}
    </Badge>
  )
}

// ── Quote Card ────────────────────────────────────────────────────────────────

function QuoteCard({ quote, index }: { quote: QuoteSummary; index: number }) {
  const navigate = useNavigate()

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.05, duration: 0.3 }}
      className="cursor-pointer"
      onClick={() => navigate(quoteDestination(quote))}
    >
      <Card className="bg-surface-raised border-border-subtle shadow-sm hover:border-border-default transition-colors duration-150 p-0">
        <CardContent className="px-6 py-5">
          <div className="flex items-start justify-between gap-4 mb-3">
            <h3 className="text-[18px] font-medium text-text-primary leading-snug flex-1">
              {quote.event}
            </h3>
            <StatusBadge status={quote.status} />
          </div>

          {(quote.date || quote.venue) && (
            <div className="flex flex-wrap gap-x-4 gap-y-1 mb-3">
              {quote.date && (
                <span className="text-[13px] text-text-tertiary">
                  {formatEventDate(quote.date)}
                </span>
              )}
              {quote.venue && (
                <span className="text-[13px] text-text-tertiary">
                  {quote.venue}
                </span>
              )}
            </div>
          )}

          <div className="flex items-center justify-between gap-4">
            <span className="font-mono text-[13px] text-text-secondary tabular-nums">
              {quote.completed_items} / {quote.total_items} items
            </span>
            <span className="text-[12px] text-text-muted">
              {formatRelativeDate(quote.created_at)}
            </span>
          </div>
        </CardContent>
      </Card>
    </motion.div>
  )
}

// ── Loading State ─────────────────────────────────────────────────────────────

function LoadingState() {
  return (
    <div className="flex items-center justify-center py-24">
      <div className="flex gap-1.5 items-center text-text-tertiary text-[14px]">
        <span className="w-1.5 h-1.5 rounded-full bg-copper animate-breathe" />
        <span className="w-1.5 h-1.5 rounded-full bg-copper animate-breathe [animation-delay:0.2s]" />
        <span className="w-1.5 h-1.5 rounded-full bg-copper animate-breathe [animation-delay:0.4s]" />
      </div>
    </div>
  )
}

// ── Empty State ───────────────────────────────────────────────────────────────

function EmptyState() {
  const navigate = useNavigate()

  return (
    <motion.div
      className="flex flex-col items-center justify-center py-32 gap-6"
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
    >
      <p className="text-[16px] text-text-tertiary">No quotes yet.</p>
      <Button
        size="lg"
        className="bg-copper hover:bg-copper-hover text-white border-0 text-[16px] px-8 py-3"
        onClick={() => navigate('/new')}
      >
        Create Your First Quote
      </Button>
    </motion.div>
  )
}

// ── Error State ───────────────────────────────────────────────────────────────

function ErrorState({ refetch }: { refetch: () => void }) {
  const navigate = useNavigate()

  return (
    <motion.div
      className="flex flex-col items-center justify-center py-24 gap-4"
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
    >
      <p className="text-[15px] text-error-text">
        Failed to load quotes. Please try again.
      </p>
      <div className="flex gap-3">
        <Button
          variant="outline"
          className="border-border-default text-text-secondary hover:border-border-strong"
          onClick={refetch}
        >
          Try Again
        </Button>
        <Button
          className="bg-copper hover:bg-copper-hover text-white border-0"
          onClick={() => navigate('/new')}
        >
          New Quote
        </Button>
      </div>
    </motion.div>
  )
}

// ── Main View ─────────────────────────────────────────────────────────────────

export default function DashboardView() {
  const navigate = useNavigate()
  const { data: quotes, isLoading, error, refetch } = useQuotes()

  return (
    <div className="min-h-screen bg-canvas px-8 pt-12 pb-20">
      <div className="max-w-[1200px] mx-auto">

        {/* ── Header ── */}
        <motion.div
          className="flex items-start justify-between mb-10"
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45 }}
        >
          <div>
            <h1 className="font-display text-[42px] font-semibold tracking-[-0.03em] text-text-primary leading-none mb-2">
              Yes Chef
            </h1>
            <p className="text-[16px] text-text-secondary">Your Quotes</p>
          </div>

          <Button
            className="bg-copper hover:bg-copper-hover text-white border-0 mt-2"
            onClick={() => navigate('/new')}
          >
            New Quote
          </Button>
        </motion.div>

        {/* ── Content ── */}
        {isLoading && <LoadingState />}

        {error && !isLoading && (
          <ErrorState refetch={refetch} />
        )}

        {!isLoading && !error && quotes && quotes.length === 0 && (
          <EmptyState />
        )}

        {!isLoading && !error && quotes && quotes.length > 0 && (
          <motion.div
            className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.3 }}
          >
            {quotes.map((quote, index) => (
              <QuoteCard key={quote.quote_id} quote={quote} index={index} />
            ))}
          </motion.div>
        )}

      </div>
    </div>
  )
}
