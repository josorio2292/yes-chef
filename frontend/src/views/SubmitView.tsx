import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { menuSpecSchema } from '../schemas'
import { useSubmitJob } from '../api'

// ── Types ───────────────────────────────────────────────────

interface ParseSummary {
  category: string
  count: number
}

// ── Icon components ─────────────────────────────────────────

function UploadIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      className="w-3.5 h-3.5 shrink-0"
    >
      <path d="M8 11V3M5 6l3-3 3 3" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M3 13h10" strokeLinecap="round" />
    </svg>
  )
}

// ── Helpers ─────────────────────────────────────────────────

function parseCategorySummary(json: unknown): ParseSummary[] {
  if (typeof json !== 'object' || json === null) return []
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
  const submitJob = useSubmitJob()

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

  // Validation errors
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
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
    setFieldErrors({})

    // Parse categories from JSON
    let categories: Record<string, unknown> = {}
    if (menuJson.trim()) {
      const result = validateJson(menuJson)
      if (!result.ok) {
        setJsonError('Fix the JSON errors before submitting.')
        return
      }
      const parsed = result.parsed as Record<string, unknown>
      categories = (parsed['categories'] as Record<string, unknown>) ?? parsed
    }

    // Validate with Zod
    const payload = {
      event: eventName.trim(),
      date: date.trim() || null,
      venue: venue.trim() || null,
      guest_count_estimate: guestCount ? parseInt(guestCount, 10) : null,
      notes: notes.trim() || null,
      categories,
    }

    const validation = menuSpecSchema.safeParse(payload)
    if (!validation.success) {
      const errors: Record<string, string> = {}
      for (const issue of validation.error.issues) {
        const key = issue.path.join('.')
        errors[key] = issue.message
      }
      setFieldErrors(errors)
      // Also surface categories error as submitError if it's the only one
      if (errors['categories'] && Object.keys(errors).length === 1) {
        setSubmitError(errors['categories'])
      }
      return
    }

    try {
      const data = await submitJob.mutateAsync(validation.data)
      navigate(`/kitchen/${data.job_id}`)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Something went wrong.'
      setSubmitError(message)
    }
  }

  const submitting = submitJob.isPending

  // ── Render ───────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-canvas flex items-start justify-center px-4 py-12">
      <div className="w-full max-w-[640px] bg-surface-raised border border-border-subtle rounded-card shadow-sm px-8 py-8">
        {/* Header */}
        <header className="mb-8">
          <h1 className="text-[28px] font-semibold tracking-tight text-text-primary mb-1">
            Yes Chef
          </h1>
          <p className="text-sm text-text-tertiary">
            Submit a menu spec — get a priced catering quote.
          </p>
        </header>

        <form onSubmit={handleSubmit} noValidate>
          {/* ── Event Details ────────────────────────────── */}
          <section className="mb-8">
            <h2 className="text-xs font-medium tracking-wide uppercase text-text-tertiary mb-4">
              Event Details
            </h2>

            <div className="grid grid-cols-2 gap-4">
              {/* Event name */}
              <div className="col-span-2 flex flex-col gap-2">
                <label
                  className="text-xs font-medium tracking-wide text-text-secondary after:content-['_*'] after:text-copper"
                  htmlFor="event-name"
                >
                  Event Name
                </label>
                <input
                  id="event-name"
                  className="bg-inset border border-border-default rounded-input px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-strong focus:ring-2 focus:ring-copper-subtle w-full"
                  type="text"
                  placeholder="e.g. The Hartley Wedding"
                  value={eventName}
                  onChange={(e) => setEventName(e.target.value)}
                  autoComplete="off"
                />
                {fieldErrors['event'] && (
                  <span className="text-error text-xs">{fieldErrors['event']}</span>
                )}
              </div>

              {/* Date */}
              <div className="flex flex-col gap-2">
                <label className="text-xs font-medium tracking-wide text-text-secondary" htmlFor="date">
                  Date
                </label>
                <input
                  id="date"
                  className="bg-inset border border-border-default rounded-input px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-border-strong focus:ring-2 focus:ring-copper-subtle w-full"
                  type="date"
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                />
              </div>

              {/* Guest count */}
              <div className="flex flex-col gap-2">
                <label className="text-xs font-medium tracking-wide text-text-secondary" htmlFor="guest-count">
                  Guest Count (est.)
                </label>
                <input
                  id="guest-count"
                  className="bg-inset border border-border-default rounded-input px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-strong focus:ring-2 focus:ring-copper-subtle w-full"
                  type="number"
                  min="1"
                  placeholder="e.g. 120"
                  value={guestCount}
                  onChange={(e) => setGuestCount(e.target.value)}
                />
              </div>

              {/* Venue */}
              <div className="col-span-2 flex flex-col gap-2">
                <label className="text-xs font-medium tracking-wide text-text-secondary" htmlFor="venue">
                  Venue
                </label>
                <input
                  id="venue"
                  className="bg-inset border border-border-default rounded-input px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-strong focus:ring-2 focus:ring-copper-subtle w-full"
                  type="text"
                  placeholder="e.g. Rooftop at The Palmer House"
                  value={venue}
                  onChange={(e) => setVenue(e.target.value)}
                  autoComplete="off"
                />
              </div>

              {/* Notes */}
              <div className="col-span-2 flex flex-col gap-2">
                <label className="text-xs font-medium tracking-wide text-text-secondary" htmlFor="notes">
                  Notes
                </label>
                <textarea
                  id="notes"
                  className="bg-inset border border-border-default rounded-input px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-strong focus:ring-2 focus:ring-copper-subtle w-full resize-y min-h-[80px] leading-relaxed"
                  placeholder="Dietary restrictions, service style, special requests…"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  rows={3}
                />
              </div>
            </div>
          </section>

          <hr className="border-none border-t border-border-subtle my-8" />

          {/* ── Menu Spec ────────────────────────────────── */}
          <section className="mb-8">
            <h2 className="text-xs font-medium tracking-wide uppercase text-text-tertiary mb-4">
              Menu Spec
            </h2>

            <div className="flex items-center gap-3 mb-4">
              <button
                type="button"
                className="inline-flex items-center gap-2 px-3 py-1.5 bg-inset border border-border-default rounded-button text-xs font-medium text-text-secondary hover:border-border-strong hover:text-text-primary transition-colors duration-150"
                onClick={() => fileInputRef.current?.click()}
              >
                <UploadIcon />
                Upload .json
              </button>
              <span className="text-xs text-text-muted tracking-wide">or paste JSON below</span>
            </div>

            <input
              ref={fileInputRef}
              type="file"
              accept=".json"
              className="hidden"
              onChange={(e) => handleFileUpload(e.target.files)}
            />

            {/* Parse summary */}
            {parseSummary.length > 0 && (
              <div className="bg-copper-subtle border border-copper/20 rounded-card px-4 py-3 mb-4">
                <p className="text-xs font-medium tracking-wide text-copper mb-2">
                  {parseSummary.reduce((sum, s) => sum + s.count, 0)} items across{' '}
                  {parseSummary.length}{' '}
                  {parseSummary.length === 1 ? 'category' : 'categories'}
                </p>
                <ul className="flex flex-wrap gap-2 list-none">
                  {parseSummary.map((s) => (
                    <li
                      key={s.category}
                      className="text-xs font-medium tracking-wide text-copper-hover bg-surface-raised border border-copper/30 rounded-badge px-2 py-0.5"
                    >
                      {s.category} ({s.count})
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="flex flex-col gap-2">
              <label className="text-xs font-medium tracking-wide text-text-secondary" htmlFor="menu-json">
                Menu JSON
              </label>
              <textarea
                id="menu-json"
                className="bg-inset border border-border-default rounded-input px-3 py-2 text-[13px] font-mono text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-strong focus:ring-2 focus:ring-copper-subtle w-full resize-y min-h-[160px] leading-relaxed"
                placeholder={`{\n  "appetizers": [\n    { "name": "Bruschetta", "servings": 2 }\n  ]\n}`}
                value={menuJson}
                onChange={(e) => handleMenuJsonChange(e.target.value)}
                spellCheck={false}
              />
              {jsonError && (
                <span className="text-error text-xs">{jsonError}</span>
              )}
            </div>
          </section>

          {/* ── Error ────────────────────────────────────── */}
          {submitError && (
            <div className="bg-error-subtle border border-error/25 rounded-card px-4 py-3 mt-4 text-sm text-error leading-relaxed">
              {submitError}
            </div>
          )}

          {/* ── Footer / CTA ─────────────────────────────── */}
          <div className="flex justify-end pt-4 border-t border-border-subtle mt-8">
            <button
              type="submit"
              className="inline-flex items-center gap-3 px-6 py-3 bg-copper hover:bg-copper-hover text-white border-none rounded-button text-base font-semibold transition-colors duration-150 disabled:opacity-55 disabled:cursor-not-allowed"
              disabled={submitting}
            >
              {submitting && (
                <span className="w-4 h-4 border-2 border-white/35 border-t-white rounded-full animate-spin" />
              )}
              {submitting ? 'Sending to kitchen…' : 'Start Quote'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
