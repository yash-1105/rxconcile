import { useCallback, useState } from 'react'
import { groupFindings, headline, phrase, type Grouped } from '../lib/phrasing'
import type { DocSide, Finding, ReconciliationResult } from '../types/api'
import { AuditPanel } from './Audit'
import { ComparisonTable } from './ComparisonTable'

/** Whether a finding's source line could be pointed at on the image. */
export type LocateResult = 'located' | 'not-located' | 'no-ref'

const TONE: Record<string, { band: string; dot: string }> = {
  clear: { band: 'border-emerald-200 bg-emerald-50/60', dot: 'bg-emerald-600' },
  warning: { band: 'border-amber-200 bg-amber-50/60', dot: 'bg-amber-500' },
  problem: { band: 'border-red-200 bg-red-50/60', dot: 'bg-red-600' },
  unknown: { band: 'border-ink-300 bg-ink-100', dot: 'bg-ink-400' },
}

/** Red is reserved for real discrepancies; grey for anything unverifiable. */
const SEVERITY_DOT: Record<string, string> = {
  critical: 'bg-red-600',
  warning: 'bg-amber-500',
  info: 'bg-ink-300',
}

function Section({
  title,
  children,
  className = '',
}: {
  title?: string
  children: React.ReactNode
  className?: string
}) {
  return (
    <section className={className}>
      {title ? (
        <h2 className="mb-3 text-xs font-semibold tracking-wider text-ink-500 uppercase">
          {title}
        </h2>
      ) : null}
      {children}
    </section>
  )
}

function Disclosure({
  summary,
  children,
  tone = 'quiet',
}: {
  summary: React.ReactNode
  children: React.ReactNode
  tone?: 'quiet' | 'muted'
}) {
  const [open, setOpen] = useState(false)
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className={`flex w-full items-center gap-2 text-left text-sm ${
          tone === 'quiet' ? 'text-ink-600 hover:text-ink-900' : 'text-ink-500 hover:text-ink-700'
        }`}
      >
        <span className="font-mono text-xs text-ink-400">{open ? '−' : '+'}</span>
        {summary}
      </button>
      {open ? <div className="mt-3">{children}</div> : null}
    </div>
  )
}

function VerdictHeader({
  result,
  grouped,
  technical,
}: {
  result: ReconciliationResult
  grouped: Grouped
  technical: boolean
}) {
  const head = headline(result, grouped)
  const tone = TONE[head.tone] ?? TONE['unknown']!
  return (
    <section className={`rounded border px-6 py-5 ${tone.band}`}>
      <div className="flex items-start gap-3">
        <span className={`mt-2 h-2.5 w-2.5 shrink-0 rounded-full ${tone.dot}`} />
        <div className="flex-1">
          <h1 className="text-2xl font-semibold tracking-tight text-ink-900">{head.title}</h1>
          <p className="mt-2 max-w-3xl text-sm text-ink-600">{head.supporting}</p>
        </div>
        <div className="shrink-0 text-right text-xs text-ink-500">
          <div>{(result.processing_ms / 1000).toFixed(1)}s</div>
          <div className="mt-0.5">{result.findings.length} findings</div>
          {/* The 0-100 score lives in the API and in technical details only: a 0
              on five criticals reads as a system failure, not as information. */}
          {technical ? (
            <div className="mt-0.5 font-mono">
              score {result.score === null ? '—' : result.score.toFixed(0)}
            </div>
          ) : null}
        </div>
      </div>
    </section>
  )
}

function DiscrepancyRow({
  finding,
  text,
  onLocate,
  technical,
}: {
  finding: Finding
  text: string
  onLocate: (finding: Finding) => LocateResult
  technical: boolean
}) {
  const [open, setOpen] = useState(false)
  const [located, setLocated] = useState<LocateResult | null>(null)
  return (
    <li className="border-b border-ink-200 last:border-b-0">
      <button
        type="button"
        onClick={() => {
          setOpen(!open)
          setLocated(onLocate(finding))
        }}
        className="flex w-full items-start gap-3 py-3 text-left hover:bg-ink-50"
      >
        <span
          className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
            SEVERITY_DOT[finding.severity] ?? 'bg-ink-300'
          }`}
        />
        <span className="flex-1 text-sm text-ink-900">{text}</span>
        {technical ? (
          <span className="shrink-0 font-mono text-xs text-ink-400">{finding.rule_code}</span>
        ) : null}
        <span className="shrink-0 font-mono text-xs text-ink-300">{open ? '−' : '+'}</span>
      </button>
      {open ? (
        <div className="pb-3 pl-5">
          {located === 'not-located' ? (
            <p className="mb-2 text-xs text-ink-500">
              This line could not be located on the image, so there is nothing to highlight.
              Read it off the page before acting on this.
            </p>
          ) : null}
          {located === 'no-ref' ? (
            <p className="mb-2 text-xs text-ink-500">
              This describes the document as a whole, not one line, so there is no region to
              highlight.
            </p>
          ) : null}
          <dl className="grid gap-x-6 gap-y-1 sm:grid-cols-[10rem_1fr]">
            {Object.entries(finding.detail).map(([key, value]) => (
              <div key={key} className="contents">
                <dt className="text-xs text-ink-500">{key}</dt>
                <dd className="font-mono text-xs break-all text-ink-700">
                  {typeof value === 'object' && value !== null
                    ? JSON.stringify(value)
                    : String(value)}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
    </li>
  )
}

export function Result({
  result,
  prescriptionImage,
  billImage,
  onReset,
  readOnly = false,
}: {
  result: ReconciliationResult
  prescriptionImage: string | null
  billImage: string | null
  onReset: () => void
  /** True when reopened from history: a record of what was reported, not a new run. */
  readOnly?: boolean
}) {
  const [technical, setTechnical] = useState(false)
  const [highlight, setHighlight] = useState<{ side: DocSide; itemId: string } | null>(null)
  const grouped = groupFindings(result.findings)
  const say = (f: Finding) => phrase(f, result.prescription, result.bill)

  const itemHasBox = useCallback(
    (side: DocSide, itemId: string): boolean => {
      const items = side === 'prescription' ? result.prescription.items : result.bill.items
      return items.find((item) => item.item_id === itemId)?.bbox != null
    },
    [result],
  )

  const locate = useCallback(
    (finding: Finding): LocateResult => {
      const target: { side: DocSide; itemId: string } | null = finding.prescribed_ref
        ? { side: 'prescription', itemId: finding.prescribed_ref }
        : finding.billed_ref
          ? { side: 'bill', itemId: finding.billed_ref }
          : null
      if (!target) {
        setHighlight(null)
        return 'no-ref'
      }
      if (!itemHasBox(target.side, target.itemId)) {
        setHighlight(null)
        return 'not-located'
      }
      setTechnical(true)
      setHighlight(target)
      return 'located'
    },
    [itemHasBox],
  )

  const hoverRow = useCallback(
    (row: { prescribedId: string | null; billedId: string | null } | null) => {
      if (!technical) return
      if (!row) return setHighlight(null)
      if (row.prescribedId && itemHasBox('prescription', row.prescribedId)) {
        setHighlight({ side: 'prescription', itemId: row.prescribedId })
      } else if (row.billedId && itemHasBox('bill', row.billedId)) {
        setHighlight({ side: 'bill', itemId: row.billedId })
      } else {
        setHighlight(null)
      }
    },
    [itemHasBox, technical],
  )

  const unverifiedCount = grouped.unverified.length
  const notRunCount = grouped.notRun.length

  return (
    <div className="space-y-10">
      {readOnly ? (
        <p className="t-small rounded bg-ink-100 px-4 py-2.5 text-muted">
          Reopened from history. This is the result exactly as it was reported at the time;
          the source images are not stored, so the audit panel has no page to show.
        </p>
      ) : null}
      <VerdictHeader result={result} grouped={grouped} technical={technical} />

      {grouped.discrepancies.length > 0 ? (
        <Section title="What is wrong">
          <ul className="rounded border border-ink-200 bg-white px-5">
            {grouped.discrepancies.map((finding, index) => (
              <DiscrepancyRow
                key={`${finding.rule_code}-${index}`}
                finding={finding}
                text={say(finding)}
                onLocate={locate}
                technical={technical}
              />
            ))}
          </ul>
        </Section>
      ) : null}

      {grouped.noted.length > 0 ? (
        <Section title="Noted">
          <ul className="rounded border border-ink-200 bg-white px-5">
            {grouped.noted.map((finding, index) => (
              <DiscrepancyRow
                key={`noted-${index}`}
                finding={finding}
                text={say(finding)}
                onLocate={locate}
                technical={technical}
              />
            ))}
          </ul>
        </Section>
      ) : null}

      <Section title="Line by line">
        <div className="rounded border border-ink-200 bg-white px-5 py-4">
          <ComparisonTable result={result} onHover={hoverRow} technical={technical} />
        </div>
      </Section>

      {/* Quiet footnotes. Never red, never amber -- an unverifiable check is not
          a problem with the bill, and must not read as one. But it must also
          never read as a check that passed. */}
      {unverifiedCount + notRunCount > 0 ? (
        <Section>
          <div className="space-y-3 rounded border border-ink-200 bg-ink-50/60 px-5 py-4">
            {unverifiedCount > 0 ? (
              <Disclosure
                summary={
                  <span>
                    Quantities could not be verified ({unverifiedCount}{' '}
                    {unverifiedCount === 1 ? 'item' : 'items'})
                  </span>
                }
              >
                <p className="mb-3 max-w-3xl text-sm text-ink-500">
                  The bill does not state whether its quantity column counts whole packs or
                  individual tablets, and the two readings lead to different conclusions. No
                  quantity discrepancy is claimed either way.
                </p>
                <ul className="space-y-1">
                  {grouped.unverified.map((finding, index) => (
                    <li key={`uv-${index}`} className="text-sm text-ink-600">
                      {say(finding)}
                      {technical ? (
                        <span className="ml-2 font-mono text-xs text-ink-400">
                          {String(finding.detail['basis_method'] ?? finding.rule_code)}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </Disclosure>
            ) : null}

            {notRunCount > 0 ? (
              <Disclosure
                tone="muted"
                summary={
                  <span>
                    {notRunCount} {notRunCount === 1 ? 'check' : 'checks'} could not run
                  </span>
                }
              >
                <p className="mb-3 max-w-3xl text-sm text-ink-500">
                  These were not performed, because the documents did not carry the values they
                  need. They are not passes.
                </p>
                <ul className="space-y-1">
                  {grouped.notRun.map((finding, index) => (
                    <li key={`nr-${index}`} className="text-sm text-ink-600">
                      {say(finding)}
                    </li>
                  ))}
                </ul>
              </Disclosure>
            ) : null}
          </div>
        </Section>
      ) : null}

      <div className="flex flex-wrap items-center gap-4 border-t border-ink-200 pt-6">
        <button
          type="button"
          onClick={onReset}
          className="rounded bg-seal px-5 py-2.5 text-sm font-semibold text-white hover:opacity-90"
        >
          {readOnly ? 'Back to a new reconciliation' : 'Reconcile another'}
        </button>
        <label className="flex cursor-pointer items-center gap-2 text-sm text-ink-500">
          <input
            type="checkbox"
            checked={technical}
            onChange={(event) => setTechnical(event.target.checked)}
            className="h-3.5 w-3.5 accent-[color:var(--color-accent)]"
          />
          Technical details
        </label>
      </div>

      {technical ? (
        <Section title="Technical details">
          <div className="space-y-6">
            <dl className="grid gap-4 rounded border border-ink-200 bg-white px-5 py-4 sm:grid-cols-4">
              {[
                ['Verdict', result.verdict],
                ['Score', result.score === null ? '— not scored' : result.score.toFixed(0)],
                [
                  'Runs',
                  `${result.prescription.run_item_counts.length} · items ${result.prescription.run_item_counts.join('/')}`,
                ],
                [
                  'Review',
                  result.review_summary.agreement_measured
                    ? `${result.review_summary.items_needing_review} items · ${result.review_summary.checks_unavailable} checks not run`
                    : 'agreement not measured',
                ],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt className="text-xs tracking-wide text-ink-500 uppercase">{label}</dt>
                  <dd className="mt-1 font-mono text-sm text-ink-900">{value}</dd>
                </div>
              ))}
            </dl>

            {grouped.quality.length > 0 ? (
              <Disclosure
                summary={<span>Extraction quality · {grouped.quality.length}</span>}
              >
                <ul className="space-y-1">
                  {grouped.quality.map((finding, index) => (
                    <li key={`q-${index}`} className="text-sm text-ink-600">
                      <span className="font-mono text-xs text-ink-400">
                        {finding.rule_code}
                      </span>{' '}
                      {finding.message}
                    </li>
                  ))}
                </ul>
              </Disclosure>
            ) : null}

            <AuditPanel
              prescription={result.prescription}
              bill={result.bill}
              prescriptionImage={prescriptionImage}
              billImage={billImage}
              highlight={highlight}
            />
          </div>
        </Section>
      ) : null}
    </div>
  )
}
