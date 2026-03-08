import { useEffect, useRef, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import './KitchenView.css'

// ── Types ────────────────────────────────────────────────────────────────────

/**
 * @typedef {'pending'|'decomposing'|'decomposed'|'resolving'|'completed'|'failed'} ItemStatus
 */

/**
 * @typedef {{ item_name: string, step: string, status: ItemStatus }} JobItem
 */

/**
 * @typedef {{
 *   job_id: string,
 *   status: string,
 *   total_items: number,
 *   completed_items: number,
 *   failed_items: number,
 *   items: JobItem[]
 * }} JobState
 */

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Map an item's status/step to a card state class */
function cardClass(item) {
  if (item.status === 'completed') return 'ticket--completed'
  if (item.status === 'failed')    return 'ticket--failed'
  if (
    item.status === 'decomposing' ||
    item.status === 'decomposed'  ||
    item.status === 'resolving'
  ) return 'ticket--processing'
  return 'ticket--pending'
}

/** Map an item's status to a human-readable station label */
function stationLabel(item) {
  switch (item.status) {
    case 'decomposing': return 'Prep — decomposing'
    case 'decomposed':  return 'Prep — decomposed'
    case 'resolving':   return 'Match — resolving'
    case 'completed':   return 'Done'
    case 'failed':      return "86'd"
    default:            return 'Waiting'
  }
}

/** Infer a category from item_name when not provided (the API gives item_name only) */
function inferCategory(name) {
  const n = name.toLowerCase()
  if (/soup|salad|bite|spring|bruschetta|cocktail shrimp|mushroom/.test(n)) return 'Appetizer'
  if (/cake|tart|mousse|crème|panna|sorbet|chocolate|dessert/.test(n)) return 'Dessert'
  if (/margarita|mojito|sangria|punch|cocktail|spritz/.test(n)) return 'Cocktail'
  return 'Main'
}

/** Group items by logical station bucket */
function groupByStation(items) {
  const prep = []
  const match = []
  const done = []
  const eightySixed = []
  const pending = []

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

// ── Ticket Card ──────────────────────────────────────────────────────────────

function TicketCard({ item }) {
  const cls = cardClass(item)
  const station = stationLabel(item)
  const category = inferCategory(item.item_name)

  return (
    <article className={`ticket ${cls}`}>
      <div className="ticket__header">
        <span className="ticket__name">{item.item_name}</span>
        <span className="ticket__tag">{category}</span>
      </div>
      <div className="ticket__station">{station}</div>
    </article>
  )
}

// ── Station Section ───────────────────────────────────────────────────────────

function Station({ label, icon, items, hidden }) {
  if (hidden) return null

  return (
    <section className={`station${items.length === 0 ? ' station--empty' : ''}`}>
      <div className="station__header">
        <span className="station__icon">{icon}</span>
        <span className="station__label">{label}</span>
        {items.length > 0 && (
          <span className="station__count">({items.length})</span>
        )}
      </div>
      {items.length > 0 && (
        <div className="station__rail">
          {items.map((item) => (
            <TicketCard key={item.item_name} item={item} />
          ))}
        </div>
      )}
    </section>
  )
}

// ── Counter ───────────────────────────────────────────────────────────────────

function Counter({ value, label, modifier }) {
  return (
    <div className={`counter counter--${modifier}`}>
      <span className="counter__value">{value}</span>
      <span className="counter__label">{label}</span>
    </div>
  )
}

// ── Main View ─────────────────────────────────────────────────────────────────

const POLL_INTERVAL = 3000

export default function KitchenView() {
  const { jobId } = useParams()
  const navigate = useNavigate()

  /** @type {[JobState | null, Function]} */
  const [job, setJob] = useState(null)
  const [connStatus, setConnStatus] = useState('connecting') // connecting | live | error | closed
  const [jobDone, setJobDone] = useState(false)

  const pollRef = useRef(null)
  const sseRef  = useRef(null)

  // ── Merge SSE item update into job state ───────────────────────────────────
  const applyItemUpdate = useCallback((itemName, patch) => {
    setJob((prev) => {
      if (!prev) return prev
      const items = prev.items.map((it) =>
        it.item_name === itemName ? { ...it, ...patch } : it,
      )
      return { ...prev, ...recalcCounters(items), items }
    })
  }, [])

  // ── Polling ────────────────────────────────────────────────────────────────
  const fetchJob = useCallback(async () => {
    try {
      const res = await fetch(`/jobs/${jobId}`)
      if (!res.ok) return
      /** @type {JobState} */
      const data = await res.json()
      setJob(data)
      if (data.status === 'completed' || data.status === 'failed') {
        setJobDone(true)
        stopPolling()
      }
    } catch {
      // network error — will retry on next tick
    }
  }, [jobId])

  function startPolling() {
    if (pollRef.current) return
    pollRef.current = setInterval(fetchJob, POLL_INTERVAL)
  }

  function stopPolling() {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  // ── SSE ────────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!jobId || jobId === 'demo') {
      // Demo mode — show placeholder state
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

    // Initial fetch
    fetchJob()

    // Start polling as fallback
    startPolling()

    // Connect SSE
    const es = new EventSource(`/jobs/${jobId}/stream`)
    sseRef.current = es

    es.addEventListener('open', () => {
      setConnStatus('live')
    })

    es.addEventListener('error', () => {
      setConnStatus('error')
      // Polling continues as fallback
    })

    es.addEventListener('item_step_change', (e) => {
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

    es.addEventListener('item_completed', (e) => {
      try {
        const payload = JSON.parse(e.data)
        applyItemUpdate(payload.item_name, { status: 'completed', step: 'completed' })
        setJob((prev) => {
          if (!prev) return prev
          const completed = prev.items.filter((i) => i.status === 'completed').length
          return { ...prev, completed_items: completed }
        })
      } catch {
        // ignore
      }
    })

    es.addEventListener('item_failed', (e) => {
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
      stopPolling()
      es.close()
      // Final authoritative fetch
      fetchJob()
    })

    return () => {
      es.close()
      stopPolling()
    }
  }, [jobId]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Derived state ──────────────────────────────────────────────────────────
  const items = job?.items ?? []
  const { pending, prep, match, done, eightySixed } = groupByStation(items)

  const totalItems     = job?.total_items ?? 0
  const completedItems = job?.completed_items ?? done.length
  const failedItems    = job?.failed_items ?? eightySixed.length
  const inProgress     = prep.length + match.length

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="kitchen">
      {/* ── Header ── */}
      <header className="kitchen__header">
        <h1 className="kitchen__title">Kitchen</h1>

        <div className="kitchen__counters">
          <Counter value={totalItems}     label="Total items"  modifier="total"  />
          <Counter value={inProgress}     label="In progress"  modifier="active"  />
          <Counter value={completedItems} label="Completed"    modifier="done"   />
          <Counter value={failedItems}    label="Failed"       modifier="failed" />
        </div>

        {jobDone && (
          <div className="kitchen__done-banner">
            <span>✓ All items processed — quote is ready.</span>
            <button
              className="btn-view-quote"
              onClick={() => navigate(`/pass/${jobId}`)}
            >
              View Quote
            </button>
          </div>
        )}
      </header>

      {/* ── Ticket rail ── */}
      <main className="kitchen__body">
        {items.length === 0 ? (
          <div className="kitchen__empty">
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
        <div className={`kitchen__conn kitchen__conn--${connStatus}`}>
          <span className="conn-dot" />
          {connStatus === 'live'       && 'Live'}
          {connStatus === 'connecting' && 'Connecting…'}
          {connStatus === 'error'      && 'Polling'}
          {connStatus === 'closed'     && 'Done'}
        </div>
      )}
    </div>
  )
}

// ── Utils ─────────────────────────────────────────────────────────────────────

/** Recount completed/failed from items array */
function recalcCounters(items) {
  const completed_items = items.filter((i) => i.status === 'completed').length
  const failed_items    = items.filter((i) => i.status === 'failed').length
  return { completed_items, failed_items }
}
