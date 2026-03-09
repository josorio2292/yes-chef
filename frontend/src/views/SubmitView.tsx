import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'motion/react'
import { menuSpecSchema } from '../schemas'
import { useSubmitJob } from '../api'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'

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
    <div className="min-h-screen bg-canvas flex items-center justify-center px-4 py-16">
      <motion.div
        className="w-full max-w-[640px]"
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: 'easeOut' }}
      >
        <Card className="bg-surface-raised border-border-subtle shadow-lg border-t-2 border-t-copper/60">
          <CardHeader className="pb-4 pt-12 px-12">
            <CardTitle className="font-display text-[42px] font-semibold tracking-[-0.03em] text-text-primary mb-1.5">
              Yes Chef
            </CardTitle>
            <CardDescription className="text-base text-text-secondary">
              Submit a menu spec — get a priced catering quote.
            </CardDescription>
          </CardHeader>

          <CardContent className="px-12 pb-12">
            <form onSubmit={handleSubmit} noValidate className="space-y-8">

              {/* ── Event Details ────────────────────────────── */}
              <section>
                <div className="flex items-center gap-3 mb-6">
                  <h2 className="text-[11px] font-medium tracking-[0.12em] uppercase text-text-tertiary">
                    Event Details
                  </h2>
                  <div className="flex-1 h-px bg-border-subtle" />
                </div>

                <div className="grid grid-cols-2 gap-x-5 gap-y-6">
                  {/* Event name */}
                  <div className="col-span-2 space-y-3">
                    <Label
                      htmlFor="event-name"
                      className="text-[13px] tracking-[0.06em] text-text-secondary after:content-['_*'] after:text-copper"
                    >
                      Event Name
                    </Label>
                    <Input
                      id="event-name"
                      type="text"
                      placeholder="e.g. The Hartley Wedding"
                      value={eventName}
                      onChange={(e) => setEventName(e.target.value)}
                      autoComplete="off"
                      className="bg-inset border-border-default text-text-primary placeholder:text-text-muted text-[15px]"
                    />
                    {fieldErrors['event'] && (
                      <span className="text-error-text text-sm">{fieldErrors['event']}</span>
                    )}
                  </div>

                  {/* Date */}
                  <div className="space-y-3">
                    <Label
                      htmlFor="date"
                      className="text-[13px] tracking-[0.06em] text-text-secondary"
                    >
                      Date
                    </Label>
                    <Input
                      id="date"
                      type="date"
                      value={date}
                      onChange={(e) => setDate(e.target.value)}
                      className="bg-inset border-border-default text-text-primary placeholder:text-text-muted text-[15px]"
                    />
                  </div>

                  {/* Guest count */}
                  <div className="space-y-3">
                    <Label
                      htmlFor="guest-count"
                      className="text-[13px] tracking-[0.06em] text-text-secondary"
                    >
                      Guest Count (est.)
                    </Label>
                    <Input
                      id="guest-count"
                      type="number"
                      min="1"
                      placeholder="e.g. 120"
                      value={guestCount}
                      onChange={(e) => setGuestCount(e.target.value)}
                      className="bg-inset border-border-default text-text-primary placeholder:text-text-muted text-[15px]"
                    />
                  </div>

                  {/* Venue */}
                  <div className="col-span-2 space-y-3">
                    <Label
                      htmlFor="venue"
                      className="text-[13px] tracking-[0.06em] text-text-secondary"
                    >
                      Venue
                    </Label>
                    <Input
                      id="venue"
                      type="text"
                      placeholder="e.g. Rooftop at The Palmer House"
                      value={venue}
                      onChange={(e) => setVenue(e.target.value)}
                      autoComplete="off"
                      className="bg-inset border-border-default text-text-primary placeholder:text-text-muted text-[15px]"
                    />
                  </div>

                  {/* Notes */}
                  <div className="col-span-2 space-y-3">
                    <Label
                      htmlFor="notes"
                      className="text-[13px] tracking-[0.06em] text-text-secondary"
                    >
                      Notes
                    </Label>
                    <Textarea
                      id="notes"
                      placeholder="Dietary restrictions, service style, special requests…"
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                      rows={3}
                      className="bg-inset border-border-default text-text-primary placeholder:text-text-muted text-[15px]"
                    />
                  </div>
                </div>
              </section>

              {/* Decorative divider */}
              <div className="flex items-center gap-3">
                <Separator className="flex-1 bg-border-subtle" />
                <span className="text-copper/40 text-xs">◆</span>
                <Separator className="flex-1 bg-border-subtle" />
              </div>

              {/* ── Menu Spec ────────────────────────────────── */}
              <section>
                <div className="flex items-center gap-3 mb-6">
                  <h2 className="text-[11px] font-medium tracking-[0.12em] uppercase text-text-tertiary">
                    Menu Spec
                  </h2>
                  <div className="flex-1 h-px bg-border-subtle" />
                </div>

                <div className="flex items-center gap-3 mb-5">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="bg-surface border-border-default text-text-secondary hover:border-border-strong hover:text-text-primary inline-flex items-center gap-2"
                    onClick={() => fileInputRef.current?.click()}
                  >
                    <UploadIcon />
                    Upload .json
                  </Button>
                  <span className="text-xs text-text-muted">or paste JSON below</span>
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
                  <div className="bg-copper-subtle border border-border-accent rounded-md p-4 mb-4">
                    <p className="text-xs font-semibold tracking-wide text-copper mb-2.5">
                      {parseSummary.reduce((sum, s) => sum + s.count, 0)} items across{' '}
                      {parseSummary.length}{' '}
                      {parseSummary.length === 1 ? 'category' : 'categories'}
                    </p>
                    <ul className={cn('flex flex-wrap gap-1.5 list-none')}>
                      {parseSummary.map((s) => (
                        <li key={s.category}>
                          <Badge
                            variant="outline"
                            className="border-border-accent text-copper italic"
                          >
                            <span className="italic">{s.category}</span>{' '}
                            <span className="text-copper/70 not-italic">({s.count})</span>
                          </Badge>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="space-y-3">
                  <Label
                    htmlFor="menu-json"
                    className="text-[13px] tracking-[0.06em] text-text-secondary"
                  >
                    Menu JSON
                  </Label>
                  <Textarea
                    id="menu-json"
                    placeholder={`{\n  "appetizers": [\n    { "name": "Bruschetta", "servings": 2 }\n  ]\n}`}
                    value={menuJson}
                    onChange={(e) => handleMenuJsonChange(e.target.value)}
                    spellCheck={false}
                    className="textarea-graph bg-inset border-border-default text-text-primary placeholder:text-text-muted text-[15px] font-mono text-[13px] min-h-[160px]"
                  />
                  {jsonError && (
                    <span className="text-error-text text-sm">{jsonError}</span>
                  )}
                </div>
              </section>

              {/* ── Error ────────────────────────────────────── */}
              {submitError && (
                <div className="bg-error-subtle border border-error/20 rounded-md px-4 py-3 text-[15px] text-error-text leading-relaxed">
                  {submitError}
                </div>
              )}

              {/* ── Footer / CTA ─────────────────────────────── */}
              <div className="border-t border-border-subtle pt-8 mt-2">
                <Button
                  type="submit"
                  disabled={submitting}
                  className="w-full h-12 bg-copper hover:bg-copper-hover text-white text-[15px] font-semibold tracking-[0.08em] uppercase"
                >
                  {submitting && (
                    <span className="w-4 h-4 border-2 border-white/25 border-t-white rounded-full animate-spin mr-3" />
                  )}
                  {submitting ? 'Sending to kitchen…' : 'Start Quote'}
                </Button>
              </div>

            </form>
          </CardContent>
        </Card>
      </motion.div>
    </div>
  )
}
