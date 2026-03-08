import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import './SubmitView.css'

// ── Types ───────────────────────────────────────────────────

interface ParseSummary {
  category: string
  count: number
}

// ── Icon components ─────────────────────────────────────────

function UploadIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M8 11V3M5 6l3-3 3 3" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M3 13h10" strokeLinecap="round" />
    </svg>
  )
}

// ── Helpers ─────────────────────────────────────────────────

function parseCategorySummary(json: unknown): ParseSummary[] {
  if (typeof json !== 'object' || json === null) return []
  // categories may be nested under a "categories" key or be the root object
  const categories =
    (json as Record<string, unknown>)['categories'] ?? json
  if (typeof categories !== 'object' || categories === null) return []
  return Object.entries(categories as Record<string, unknown>)
    .map(([cat, items]) => ({
      category: cat,
      count: Array.isArray(items) ? items.length : 0,
    }))
    .filter((s) => s.count > 0)
}

// ── Component ───────────────────────────────────────────────

export default function SubmitView() {
  const navigate = useNavigate()
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Event details
  const [eventName, setEventName] = useState('')
  const [date, setDate] = useState('')
  const [venue, setVenue] = useState('')
  const [guestCount, setGuestCount] = useState('')
  const [notes, setNotes] = useState('')

  // Menu spec
  const [menuJson, setMenuJson] = useState('')
  const [jsonError, setJsonError] = useState('')
  const [parseSummary, setParseSummary] = useState<ParseSummary[]>([])

  // Submission state
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')

  // ── JSON validation helper ───────────────────────────────

  function validateJson(raw: string): { ok: boolean; parsed?: unknown } {
    if (!raw.trim()) return { ok: true }
    try {
      const parsed = JSON.parse(raw)
      return { ok: true, parsed }
    } catch {
      return { ok: false }
    }
  }

  // ── Menu JSON textarea change ────────────────────────────

  function handleMenuJsonChange(value: string) {
    setMenuJson(value)
    setJsonError('')
    setParseSummary([])
    if (!value.trim()) return
    const result = validateJson(value)
    if (!result.ok) {
      setJsonError('Invalid JSON — check for missing brackets or commas')
      return
    }
    const summary = parseCategorySummary(result.parsed)
    setParseSummary(summary)
  }

  // ── File upload ──────────────────────────────────────────

  function handleFileUpload(files: FileList | null) {
    if (!files || files.length === 0) return
    const file = files[0]
    if (!file.name.endsWith('.json')) {
      setJsonError('Only .json files are supported')
      return
    }
    const reader = new FileReader()
    reader.onload = (e) => {
      const text = (e.target?.result as string) ?? ''
      handleMenuJsonChange(text)
    }
    reader.readAsText(file)
  }

  // ── Submit ───────────────────────────────────────────────

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitError('')

    // Validate event name
    if (!eventName.trim()) {
      setSubmitError('Event name is required.')
      return
    }

    // Validate menu JSON
    let categories: Record<string, unknown> = {}
    if (menuJson.trim()) {
      const result = validateJson(menuJson)
      if (!result.ok) {
        setJsonError('Fix the JSON errors before submitting.')
        return
      }
      const parsed = result.parsed as Record<string, unknown>
      // Accept { categories: {...} } or bare { category: [...] }
      categories = (parsed['categories'] as Record<string, unknown>) ?? parsed
    }

    setSubmitting(true)
    try {
      const body = {
        event: eventName.trim(),
        date: date.trim() || null,
        venue: venue.trim() || null,
        guest_count_estimate: guestCount ? parseInt(guestCount, 10) : null,
        notes: notes.trim() || null,
        categories,
      }

      const res = await fetch('/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })

      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as { detail?: string }
        throw new Error(data.detail ?? `Server error ${res.status}`)
      }

      const data = (await res.json()) as { job_id: string }
      navigate(`/kitchen/${data.job_id}`)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Something went wrong.'
      setSubmitError(message)
    } finally {
      setSubmitting(false)
    }
  }

  // ── Render ───────────────────────────────────────────────

  return (
    <div className="submit-view">
      <div className="submit-card">
        {/* Header */}
        <header className="submit-header">
          <h1 className="submit-header__title">Yes Chef</h1>
          <p className="submit-header__subtitle">
            Submit a menu spec — get a priced catering quote.
          </p>
        </header>

        <form onSubmit={handleSubmit} noValidate>
          {/* ── Event Details ────────────────────────────── */}
          <section className="submit-section">
            <h2 className="submit-section__heading">Event Details</h2>

            <div className="form-grid">
              {/* Event name */}
              <div className="field field--full">
                <label className="field__label field__label--required" htmlFor="event-name">
                  Event Name
                </label>
                <input
                  id="event-name"
                  className="field__input"
                  type="text"
                  placeholder="e.g. The Hartley Wedding"
                  value={eventName}
                  onChange={(e) => setEventName(e.target.value)}
                  autoComplete="off"
                />
              </div>

              {/* Date */}
              <div className="field">
                <label className="field__label" htmlFor="date">
                  Date
                </label>
                <input
                  id="date"
                  className="field__input"
                  type="date"
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                />
              </div>

              {/* Guest count */}
              <div className="field">
                <label className="field__label" htmlFor="guest-count">
                  Guest Count (est.)
                </label>
                <input
                  id="guest-count"
                  className="field__input"
                  type="number"
                  min="1"
                  placeholder="e.g. 120"
                  value={guestCount}
                  onChange={(e) => setGuestCount(e.target.value)}
                />
              </div>

              {/* Venue */}
              <div className="field field--full">
                <label className="field__label" htmlFor="venue">
                  Venue
                </label>
                <input
                  id="venue"
                  className="field__input"
                  type="text"
                  placeholder="e.g. Rooftop at The Palmer House"
                  value={venue}
                  onChange={(e) => setVenue(e.target.value)}
                  autoComplete="off"
                />
              </div>

              {/* Notes */}
              <div className="field field--full">
                <label className="field__label" htmlFor="notes">
                  Notes
                </label>
                <textarea
                  id="notes"
                  className="field__textarea"
                  placeholder="Dietary restrictions, service style, special requests…"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  rows={3}
                />
              </div>
            </div>
          </section>

          <hr className="submit-divider" />

          {/* ── Menu Spec ────────────────────────────────── */}
          <section className="submit-section">
            <h2 className="submit-section__heading">Menu Spec</h2>

            <div className="menu-spec__actions">
              <button
                type="button"
                className="upload-btn"
                onClick={() => fileInputRef.current?.click()}
              >
                <UploadIcon />
                Upload .json
              </button>
              <span className="menu-spec__hint">or paste JSON below</span>
            </div>

            <input
              ref={fileInputRef}
              type="file"
              accept=".json"
              style={{ display: 'none' }}
              onChange={(e) => handleFileUpload(e.target.files)}
            />

            {/* Parse summary */}
            {parseSummary.length > 0 && (
              <div className="parse-summary">
                <p className="parse-summary__title">
                  {parseSummary.reduce((sum, s) => sum + s.count, 0)} items across{' '}
                  {parseSummary.length} {parseSummary.length === 1 ? 'category' : 'categories'}
                </p>
                <ul className="parse-summary__list">
                  {parseSummary.map((s) => (
                    <li key={s.category} className="parse-summary__tag">
                      {s.category} ({s.count})
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="field">
              <label className="field__label" htmlFor="menu-json">
                Menu JSON
              </label>
              <textarea
                id="menu-json"
                className="field__textarea field__textarea--mono"
                placeholder={`{\n  "appetizers": [\n    { "name": "Bruschetta", "servings": 2 }\n  ]\n}`}
                value={menuJson}
                onChange={(e) => handleMenuJsonChange(e.target.value)}
                spellCheck={false}
              />
              {jsonError && <span className="field__error">{jsonError}</span>}
            </div>
          </section>

          {/* ── Error ────────────────────────────────────── */}
          {submitError && <div className="submit-error">{submitError}</div>}

          {/* ── Footer / CTA ─────────────────────────────── */}
          <div className="submit-footer">
            <button type="submit" className="btn-cta" disabled={submitting}>
              {submitting && <span className="btn-cta__spinner" />}
              {submitting ? 'Sending to kitchen…' : 'Start Quote'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
