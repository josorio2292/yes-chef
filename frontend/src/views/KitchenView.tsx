import { useEffect, useRef, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useJobStatus } from '../api'
import type { JobStatus } from '../schemas'

// ── Types ────────────────────────────────────────────────────────────────────

type ItemStatus = 'pending' | 'decomposing' | 'decomposed' | 'resolving' | 'completed' | 'failed'

interface JobItem {
  item_name: string
  step: string
  status: ItemStatus
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function cardStateClasses(item: JobItem): string {
  if (item.status === 'completed') return 'border-l-success'
  if (item.status === 'failed') return 'border-l-error'
  if (
    item.status === 'decomposing' ||
    item.status === 'decomposed' ||
    item.status === 'resolving'
  ) return 'border-l-copper'
  return 'border-l-border-default'
}

function stationLabel(item: JobItem): string {
  switch (item.status) {
    case 'decomposing': return 'Prep — decomposing'
    case 'decomposed':  return 'Prep — decomposed'
    case 'resolving':   return 'Match — resolving'
    case 'completed':   return 'Done'
    case 'failed':      return "86'd"
    default:            return 'Waiting'
  }
}

function stationLabelColor(item: JobItem): string {
  if (item.status === 'completed') return 'text-success font-medium'
  if (item.status === 'failed') return 'text-error font-medium'
  if (
    item.status === 'decomposing' ||
    item.status === 'decomposed' ||
    item.status === 'resolving'
  ) return 'text-copper font-medium'
  return 'text-text-tertiary'
}

function inferCategory(name: string): string {
  const n = name.toLowerCase()
  if (/soup|salad|bite|spring|bruschetta|cocktail shrimp|mushroom/.test(n)) return 'Appetizer'
  if (/cake|tart|mousse|crème|panna|sorbet|chocolate|dessert/.test(n)) return 'Dessert'
  if (/margarita|mojito|sangria|punch|cocktail|spritz/.test(n)) return 'Cocktail'
  return 'Main'
}

function groupByStation(items: JobItem[]) {
  const prep: JobItem[] = []
  const match: JobItem[] = []
  const done: JobItem[] = []
  const eightySixed: JobItem[] = []
  const pending: JobItem[] = []

  for (const item of items) {
    switch (item.status) {
      case 'decomposing':
      case 'decomposed':
        prep.push(item)
        break
      case 'resolving':
        match.push(item)
        break
      case 'completed':
        done.push(item)
        break
      case 'failed':
        eightySixed.push(item)
        break
      default:
        pending.push(item)
    }
  }

  return { pending, prep, match, done, eightySixed }
}

function recalcCounters(items: JobItem[]) {
  const completed_items = items.filter((i) => i.status === 'completed').length
  const failed_items = items.filter((i) => i.status === 'failed').length
  return { completed_items, failed_items }
}

// ── Ticket Card ──────────────────────────────────────────────────────────────

function TicketCard({ item }: { item: JobItem }) {
  const borderColor = cardStateClasses(item)
  const labelColor = stationLabelColor(item)
  const station = stationLabel(item)
  const category = inferCategory(item.item_name)
  const isProcessing =
    item.status === 'decomposing' ||
    item.status === 'decomposed' ||
    item.status === 'resolving'

  return (
    <article
      className={`bg-surface-raised border border-border-subtle border-l-[3px] ${borderColor} rounded-card shadow-sm p-4 w-[220px] min-w-[200px] transition-all duration-200 ${isProcessing ? 'animate-pulse-copper' : ''}`}
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        <span className={`text-base font-medium leading-tight flex-1 ${item.status === 'pending' ? 'text-text-muted' : 'text-text-primary'}`}>
          {item.item_name}
        </span>
        <span className="text-xs font-medium tracking-wide text-text-secondary bg-surface border border-border-subtle rounded-badge px-2 py-0.5 whitespace-nowrap shrink-0">
          {category}
        </span>
      </div>
      <div className={`text-xs tracking-wide mt-1 ${labelColor}`}>
        {station}
      </div>
    </article>
  )
}

// ── Station Section ───────────────────────────────────────────────────────────

interface StationProps {
  label: string
  icon: string
  items: JobItem[]
  hidden: boolean
}

function Station({ label, icon, items, hidden }: StationProps) {
  if (hidden) return null

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <span className="text-sm text-text-tertiary">{icon}</span>
        <span className="text-xs font-medium tracking-wide uppercase text-text-tertiary">
          {label}
        </span>
        {items.length > 0 && (
          <span className="text-[11px] tracking-wide text-text-muted ml-1">
            ({items.length})
          </span>
        )}
      </div>
      {items.length > 0 && (
        <div className="flex flex-wrap gap-4">
          {items.map((item) => (
            <TicketCard key={item.item_name} item={item} />
          ))}
        </div>
      )}
    </section>
  )
}

// ── Counter ───────────────────────────────────────────────────────────────────

interface CounterProps {
  value: number
  label: string
  valueColor?: string
}

function Counter({ value, label, valueColor = 'text-text-primary' }: CounterProps) {
  return (
    <div className="flex flex-col gap-1">
      <span className={`text-[28px] font-semibold tracking-tight tabular-nums leading-none ${valueColor}`}>
        {value}
      </span>
      <span className="text-xs font-medium tracking-wide uppercase text-text-tertiary">
        {label}
      </span>
    </div>
  )
}

// ── Main View ─────────────────────────────────────────────────────────────────

export default function KitchenView() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()

  const [job, setJob] = useState<JobStatus | null>(null)
  const [connStatus, setConnStatus] = useState<'connecting' | 'live' | 'error' | 'closed'>('connecting')
  const [jobDone, setJobDone] = useState(false)

  const sseRef = useRef<EventSource | null>(null)

  // TanStack Query polling — used as initial fetch + fallback
  const { data: queryData } = useJobStatus(jobId ?? '', !!jobId && jobId !== 'demo')

  // Merge query data into local state (SSE takes priority for real-time updates)
  useEffect(() => {
    if (!queryData) return
    setJob(queryData)
    if (queryData.status === 'completed' || queryData.status === 'completed_with_errors') {
      setJobDone(true)
    }
  }, [queryData])

  // ── Merge SSE item update into job state ───────────────────────────────────
  const applyItemUpdate = useCallback((itemName: string, patch: Partial<JobItem>) => {
    setJob((prev) => {
      if (!prev) return prev
      const items = prev.items.map((it) =>
        it.item_name === itemName ? { ...it, ...patch } : it,
      ) as JobItem[]
      return { ...prev, ...recalcCounters(items), items }
    })
  }, [])

  // ── SSE ────────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!jobId || jobId === 'demo') {
      setJob({
        job_id: 'demo',
        status: 'running',
        total_items: 0,
        completed_items: 0,
        failed_items: 0,
        items: [],
      })
      return
    }

    const es = new EventSource(`/api/jobs/${jobId}/stream`)
    sseRef.current = es

    es.addEventListener('open', () => {
      setConnStatus('live')
    })

    es.addEventListener('error', () => {
      setConnStatus('error')
    })

    es.addEventListener('item_step_change', (e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data)
        applyItemUpdate(payload.item_name, {
          status: payload.status,
          step: payload.step ?? payload.status,
        })
      } catch {
        // malformed event — ignore
      }
    })

    es.addEventListener('item_completed', (e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data)
        applyItemUpdate(payload.item_name, { status: 'completed', step: 'completed' })
      } catch {
        // ignore
      }
    })

    es.addEventListener('item_failed', (e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data)
        applyItemUpdate(payload.item_name, { status: 'failed', step: 'failed' })
      } catch {
        // ignore
      }
    })

    es.addEventListener('job_completed', () => {
      setJobDone(true)
      setConnStatus('closed')
      es.close()
    })

    return () => {
      es.close()
    }
  }, [jobId, applyItemUpdate])

  // ── Derived state ──────────────────────────────────────────────────────────
  const items = (job?.items ?? []) as JobItem[]
  const { pending, prep, match, done, eightySixed } = groupByStation(items)

  const totalItems     = job?.total_items ?? 0
  const completedItems = job?.completed_items ?? done.length
  const failedItems    = job?.failed_items ?? eightySixed.length
  const inProgress     = prep.length + match.length

  // ── Conn dot color ─────────────────────────────────────────────────────────
  const connDotColor = connStatus === 'live' ? 'bg-success'
    : connStatus === 'error' ? 'bg-error'
    : 'bg-text-muted'

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-canvas flex flex-col">
      {/* ── Header ── */}
      <header className="bg-surface-raised border-b border-border-subtle px-8 py-6 shadow-sm">
        <h1 className="text-[28px] font-semibold tracking-tight text-text-primary mb-6">
          Kitchen
        </h1>

        <div className="flex gap-8 flex-wrap">
          <Counter value={totalItems}     label="Total items"  valueColor="text-text-primary"  />
          <Counter value={inProgress}     label="In progress"  valueColor="text-copper"         />
          <Counter value={completedItems} label="Completed"    valueColor="text-success"        />
          <Counter value={failedItems}    label="Failed"       valueColor="text-error"          />
        </div>

        {jobDone && (
          <div className="mt-4 px-4 py-3 bg-success-subtle border border-success rounded-button flex items-center gap-4 text-sm font-medium text-success">
            <span>✓ All items processed — quote is ready.</span>
            <button
              className="px-3 py-1.5 bg-success text-white border-none rounded-button text-sm font-medium cursor-pointer hover:opacity-90 transition-opacity"
              onClick={() => navigate(`/pass/${jobId}`)}
            >
              View Quote
            </button>
          </div>
        )}
      </header>

      {/* ── Ticket rail ── */}
      <main className="flex-1 p-8 flex flex-col gap-8">
        {items.length === 0 ? (
          <div className="flex items-center justify-center py-16 text-text-muted text-sm">
            {jobId === 'demo'
              ? 'Submit a menu to start tracking progress.'
              : 'Loading items…'}
          </div>
        ) : (
          <>
            <Station
              label="Prep"
              icon="🔪"
              items={prep}
              hidden={prep.length === 0 && pending.length === 0 && done.length + eightySixed.length === items.length}
            />
            <Station label="Pending" icon="⏳" items={pending} hidden={pending.length === 0} />
            <Station label="Match"   icon="🔍" items={match}   hidden={match.length === 0} />
            <Station label="Done"    icon="✓"  items={done}    hidden={done.length === 0} />
            <Station label="86'd"    icon="✗"  items={eightySixed} hidden={eightySixed.length === 0} />
          </>
        )}
      </main>

      {/* ── Connection status indicator ── */}
      {jobId !== 'demo' && (
        <div className="fixed bottom-4 right-4 text-[11px] tracking-wide px-2 py-1 rounded-badge border border-border-subtle bg-surface-raised text-text-muted flex items-center gap-1.5">
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${connDotColor}`} />
          {connStatus === 'live'       && 'Live'}
          {connStatus === 'connecting' && 'Connecting…'}
          {connStatus === 'error'      && 'Polling'}
          {connStatus === 'closed'     && 'Done'}
        </div>
      )}
    </div>
  )
}
