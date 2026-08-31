/**
 * The whole top of the results screen, in one card.
 *
 * Five blocks used to stack here — a completeness banner, a verdict card, four
 * count tiles, four allowance cards and two exclusion cards — repeating each
 * other and leaving the reader to assemble the answer. This panel states it
 * once: what the documents say on the left, what it costs on the right.
 *
 * Presentation only. Every figure is passed in already computed; nothing is
 * derived here except the proportions of the bar, which are a drawing
 * instruction rather than a number anybody reads.
 */

import { useEffect, useRef, useState } from 'react'
import { SpineMark, type SpineState } from './Spine'
import type { DocumentGap } from '../lib/phrasing'
import type { RowCounts, RowFilter } from '../lib/rows'
import { balanceAfter, barSegments } from '../lib/allowanceBar'
import type { AllowanceView } from '../types/api'

const COUNT_UP_MS = 360

function reducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true
  )
}

/**
 * A figure that counts up once, then tracks its value directly.
 *
 * Only once: "This claim" changes on every accept and reject, and re-running
 * the animation on each click would turn a live figure into a distraction.
 */
function useCountUp(value: number, animate: boolean): number {
  const [shown, setShown] = useState(() => (animate ? 0 : value))
  const [settled, setSettled] = useState(!animate)
  // Once the intro is done the figure follows its value exactly, adjusted
  // during render rather than in an effect so a click never shows a stale
  // number for a frame.
  const [tracked, setTracked] = useState(value)
  if (settled && tracked !== value) {
    setTracked(value)
    setShown(value)
  }
  // Written after each render, never during one, so the animation frame below
  // always eases towards the value the panel is currently showing.
  const target = useRef(value)
  useEffect(() => {
    target.current = value
  })

  useEffect(() => {
    if (settled) return
    if (reducedMotion()) {
      setShown(target.current)
      setSettled(true)
      return
    }
    let frame = 0
    const started = performance.now()
    const step = (now: number) => {
      const t = Math.min(1, (now - started) / COUNT_UP_MS)
      // Ease out: fast to begin with, so the figure is readable almost at once.
      setShown(target.current * (1 - (1 - t) * (1 - t)))
      if (t < 1) {
        frame = requestAnimationFrame(step)
      } else {
        setShown(target.current)
        setSettled(true)
      }
    }
    frame = requestAnimationFrame(step)
    // A browser that backgrounds this tab stops firing animation frames, and
    // the count-up would then sit at zero for as long as the tab is away —
    // showing a balance of nothing where there is money. The figure lands
    // whether or not a single frame ever runs.
    const land = setTimeout(() => {
      setShown(target.current)
      setSettled(true)
    }, COUNT_UP_MS + 400)
    return () => {
      cancelAnimationFrame(frame)
      clearTimeout(land)
    }
  }, [settled])

  return shown
}

function money(amount: number | string, currency = 'INR'): string {
  const value = Number(amount)
  return `${currency} ${
    Number.isFinite(value)
      ? value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      : amount
  }`
}

/**
 * The completeness gap, as a strip along the top edge of the panel.
 *
 * Marked `unchecked`, not as a warning: nothing is wrong with the bill. The
 * checks simply could not run, and the reserved meanings of red and amber say
 * this is neither. It is given the full width of the panel because the reader
 * has to see it before the verdict underneath — a screen reporting no problems
 * with tests nobody examined is worse than no screen at all.
 */
function GapStrip({ gaps }: { gaps: DocumentGap[] }) {
  if (gaps.length === 0) return null
  return (
    <div className="border-b border-dashed border-unknown bg-ink-50 px-7 py-4">
      {gaps.map((gap) => (
        <div key={gap.title} className="flex items-start gap-3">
          <SpineMark state="unchecked" className="mt-1" />
          <p className="t-small text-muted">
            <span className="font-semibold text-ink">{gap.title}.</span> {gap.detail}
          </p>
        </div>
      ))}
    </div>
  )
}

/** One clickable count. Filters the table below, as the old tile did. */
function Count({
  value,
  label,
  tone,
  selected,
  onClick,
}: {
  value: number
  label: string
  tone: 'matched' | 'problems'
  selected: boolean
  onClick: () => void
}) {
  // A zero problem count is not alarming, so it is not red. Red is reserved
  // for a real discrepancy and nothing else.
  const colour =
    tone === 'problems' ? (value > 0 ? 'text-flag' : 'text-muted') : 'text-seal'
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={`t-small rounded px-2 py-0.5 transition-colors hover:bg-ink-100 ${
        selected ? 'bg-ink-100 font-semibold' : ''
      }`}
    >
      <span className={`font-semibold ${colour}`}>{value}</span>{' '}
      <span className="text-muted">{label}</span>
    </button>
  )
}

/** The allowance year explanation, on an info icon rather than in body text. */
function YearInfo({ allowance }: { allowance: AllowanceView }) {
  const title =
    `Allowance year ${allowance.year} (${allowance.year_starts} to ${allowance.year_ends}). ` +
    `Used so far is the total of accepted lines on this employee's ${allowance.scans_counted} ` +
    `earlier ${allowance.scans_counted === 1 ? 'claim' : 'claims'} in that window. ` +
    'Rejected lines are not counted.'
  return (
    <span
      title={title}
      aria-label={title}
      tabIndex={0}
      className="ml-1.5 inline-flex h-4 w-4 cursor-help items-center justify-center rounded-full border border-ink-300 align-middle text-[10px] font-semibold text-muted"
    >
      i
    </span>
  )
}

/**
 * Used, this claim and what is left, as proportions of the annual allowance.
 *
 * The bar exists because "you have most of your allowance left" is the thing
 * the employee came to find out, and four numbers do not say it at a glance.
 */
function AllowanceBar({
  annual,
  used,
  claim,
  animate,
}: {
  annual: number
  used: number
  claim: number
  animate: boolean
}) {
  const [drawn, setDrawn] = useState(!animate)
  useEffect(() => {
    if (drawn) return
    const frame = requestAnimationFrame(() => setDrawn(true))
    return () => cancelAnimationFrame(frame)
  }, [drawn])

  const { used: usedPct, claim: claimPct } = barSegments(annual, used, claim)
  const overdrawn = used + claim > annual

  return (
    <div
      className="mt-3 flex h-2.5 w-full overflow-hidden rounded-full bg-ink-100"
      role="img"
      aria-label={`${Math.round(usedPct)}% used, ${Math.round(claimPct)}% claimed here, the rest remaining`}
    >
      <div
        className="h-full bg-ink-400 transition-[width] duration-500 ease-out"
        style={{ width: drawn ? `${usedPct}%` : '0%' }}
      />
      <div
        className={`h-full transition-[width] duration-500 ease-out ${
          overdrawn ? 'bg-flag' : 'bg-seal'
        }`}
        style={{ width: drawn ? `${claimPct}%` : '0%' }}
      />
    </div>
  )
}

function Money({ value, currency, animate }: { value: number; currency: string; animate: boolean }) {
  return <>{money(useCountUp(value, animate), currency)}</>
}

export function SummaryPanel({
  gaps,
  head,
  submission,
  manualChecks,
  medicines,
  tests,
  filters,
  onPick,
  allowance,
  claim,
  currency = 'INR',
  animate,
}: {
  gaps: DocumentGap[]
  head: { title: string; supporting: string; tone: string }
  submission?: { condition?: string | null; description?: string | null } | null
  manualChecks: number
  medicines: RowCounts
  tests: RowCounts
  filters: { medicines: RowFilter; tests: RowFilter }
  onPick: (table: 'medicines' | 'tests', filter: RowFilter) => void
  allowance: AllowanceView | null
  claim: number
  currency?: string
  animate: boolean
}) {
  const mark: SpineState =
    head.tone === 'problem'
      ? 'problem'
      : head.tone === 'warning'
        ? 'warning'
        : head.tone === 'clear'
          ? 'clean'
          : 'unchecked'

  const annual = allowance ? Number(allowance.annual_amount) : 0
  const used = allowance ? Number(allowance.used) : 0
  const remaining = balanceAfter(annual, used, claim)
  const overdrawn = allowance !== null && used + claim > annual

  const rows: Array<{ table: 'medicines' | 'tests'; label: string; counts: RowCounts }> = [
    { table: 'medicines', label: 'Medicines', counts: medicines },
    { table: 'tests', label: 'Lab tests', counts: tests },
  ]

  return (
    <section className="overflow-hidden rounded-xl bg-surface shadow-[0_1px_2px_rgba(13,18,17,0.04),0_8px_24px_-12px_rgba(13,18,17,0.18)]">
      <GapStrip gaps={gaps} />

      <div className="grid lg:grid-cols-[1.35fr_1fr]">
        {/* LEFT — what the documents say */}
        <div className="px-7 py-7">
          <div className="flex items-start gap-4">
            <SpineMark state={mark} className="mt-2.5" />
            <div className="min-w-0 flex-1">
              <h1 className="t-display text-ink">{head.title}</h1>
              <p className="t-body mt-2 text-muted">{head.supporting}</p>

              {submission?.condition || submission?.description ? (
                <p className="t-small mt-2 text-muted">
                  {submission.condition ? (
                    <span className="font-medium text-ink">{submission.condition}</span>
                  ) : null}
                  {submission.condition && submission.description ? ' — ' : null}
                  {submission.description}
                </p>
              ) : null}

              {/* Must never disappear. A check that did not run is not a check
                  that passed. */}
              {manualChecks > 0 ? (
                <p className="t-small mt-2 text-muted">
                  {manualChecks} {manualChecks === 1 ? 'item needs' : 'items need'} a manual
                  check.
                </p>
              ) : null}
            </div>
          </div>

          <dl className="mt-6 space-y-1.5 border-t border-ink-100 pt-5">
            {rows.map((row) => (
              <div key={row.table} className="flex items-baseline gap-3">
                <dt className="t-small w-24 shrink-0 text-muted">{row.label}</dt>
                <dd className="-ml-2 flex flex-wrap items-baseline">
                  <Count
                    value={row.counts.matched}
                    label="matched"
                    tone="matched"
                    selected={filters[row.table] === 'matched'}
                    onClick={() =>
                      onPick(row.table, filters[row.table] === 'matched' ? 'all' : 'matched')
                    }
                  />
                  <span className="t-small hidden px-0.5 text-ink-300 sm:inline">·</span>
                  <Count
                    value={row.counts.problems}
                    label="problems"
                    tone="problems"
                    selected={filters[row.table] === 'problems'}
                    onClick={() =>
                      onPick(row.table, filters[row.table] === 'problems' ? 'all' : 'problems')
                    }
                  />
                </dd>
              </div>
            ))}
          </dl>
        </div>

        {/* RIGHT — the money */}
        <div className="border-t border-ink-100 bg-ink-50/60 px-7 py-7 lg:border-l lg:border-t-0">
          {allowance === null ? (
            <>
              <p className="t-micro text-muted">Balance remaining</p>
              <p className="t-hero mt-1 text-unknown">Not available</p>
              <p className="t-small mt-3 text-muted">
                This employee&rsquo;s allowance could not be loaded, so nothing here states
                what is left. This claim is {money(claim, currency)}.
              </p>
            </>
          ) : (
            <>
              <p className="t-micro text-muted">
                Balance remaining
                <YearInfo allowance={allowance} />
              </p>
              <p className={`t-hero mt-1 ${overdrawn ? 'text-flag' : 'text-ink'}`}>
                <Money value={remaining} currency={currency} animate={animate} />
              </p>

              <AllowanceBar annual={annual} used={used} claim={claim} animate={animate} />

              <dl className="mt-4 flex flex-wrap gap-x-6 gap-y-2">
                {[
                  { label: 'Allowance', value: annual, tone: 'text-ink' },
                  { label: 'Used', value: used, tone: 'text-muted' },
                  { label: 'This claim', value: claim, tone: overdrawn ? 'text-flag' : 'text-seal' },
                ].map((item) => (
                  <div key={item.label}>
                    <dt className="t-micro text-muted">{item.label}</dt>
                    <dd className={`t-small font-semibold ${item.tone}`}>
                      {money(item.value, currency)}
                    </dd>
                  </div>
                ))}
              </dl>

              {overdrawn ? (
                <p className="t-small mt-3 text-flag">
                  This claim takes the year past the allowance.
                </p>
              ) : null}
            </>
          )}
        </div>
      </div>
    </section>
  )
}

/**
 * What is not part of the claim, as one quiet line.
 *
 * These are context, not headline figures — but they are not hidden either:
 * the amount is stated on the closed line, so nobody has to open it to learn
 * that money was set aside.
 */
export function ExcludedLine({
  notOnPrescription,
  notMedicine,
  currency = 'INR',
}: {
  notOnPrescription: { total: string; lines: number }
  notMedicine: { total: string; lines: number }
  currency?: string
}) {
  const [open, setOpen] = useState(false)
  const rows = [
    {
      label: 'Not on prescription',
      body: 'Billed items with nothing on the prescription behind them.',
      ...notOnPrescription,
    },
    {
      label: 'Not a medicine',
      body: 'Cosmetics, devices, supplements and charges.',
      ...notMedicine,
    },
  ].filter((row) => row.lines > 0)

  if (rows.length === 0) return null
  const total = rows.reduce((sum, row) => sum + Number(row.total), 0)

  return (
    <div className="mt-3">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="t-small text-left text-muted underline decoration-ink-300 underline-offset-4 hover:text-ink"
      >
        {money(total, currency)} excluded from the claim — see details
      </button>
      {open ? (
        <dl className="mt-3 space-y-3 rounded-lg bg-ink-50 px-5 py-4">
          {rows.map((row) => (
            <div key={row.label}>
              <dt className="t-small font-semibold text-ink">
                {row.label} — {money(row.total, currency)}
              </dt>
              <dd className="t-small text-muted">
                {row.lines} {row.lines === 1 ? 'line' : 'lines'}. {row.body}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
  )
}
