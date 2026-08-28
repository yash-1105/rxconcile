/**
 * The results screen: summary, analysis, tables.
 *
 * Presentation only. Every number here is the number the engine computed; this
 * file decides ordering, wording and what leads. Three rules it must not break:
 *
 * 1. A check that could not run never renders as a check that passed, and is
 *    never coloured as a finding. It is also never deleted.
 * 2. The 0-100 score lives behind the technical toggle entirely. A score of 0
 *    on five criticals reads as a system failure rather than as information.
 * 3. A document that was not supplied is stated at the TOP, not in a footnote.
 *    A screen reporting no problems with medicines nobody examined is worse
 *    than no screen at all.
 */

import { useCallback, useState } from 'react'
import { documentGaps, groupFindings, headline, phrase, type Grouped } from '../lib/phrasing'
import type { DocSide, Finding, ReconciliationResult } from '../types/api'
import { AuditPanel } from './Audit'
import { SpineMark, type SpineState } from './Spine'
import { ExportBar } from './Export'
import { Reimbursement } from './Reimbursement'
import { LabTestsTable, MedicinesTable } from './Tables'

/** Whether a finding's source line could be pointed at on the image. */
export type LocateResult = 'located' | 'not-located' | 'no-ref'

const HEAD_STATE: Record<string, SpineState> = {
  clear: 'clean',
  warning: 'warning',
  problem: 'problem',
  unknown: 'unchecked',
}

function Section({
  title,
  children,
  note,
}: {
  title: string
  children: React.ReactNode
  note?: string
}) {
  return (
    <section>
      <h2 className="t-micro mb-1 text-muted">{title}</h2>
      {note ? <p className="t-small mb-3 max-w-3xl text-muted">{note}</p> : null}
      <div className={note ? '' : 'mt-3'}>{children}</div>
    </section>
  )
}

function Disclosure({
  summary,
  children,
  defaultOpen = false,
}: {
  summary: React.ReactNode
  children: React.ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 text-left text-muted hover:text-ink"
      >
        <span className="t-data w-3 shrink-0 text-unknown">{open ? '−' : '+'}</span>
        <span className="t-small">{summary}</span>
      </button>
      {open ? <div className="mt-3 pl-5">{children}</div> : null}
    </div>
  )
}

/** SECTION 1 — the plain-language verdict. No score, no rule codes. */
function Summary({ result, grouped }: { result: ReconciliationResult; grouped: Grouped }) {
  const head = headline(result, grouped)
  return (
    <section className="rounded border border-ink-200 bg-surface px-6 py-5">
      <div className="flex items-start gap-4">
        <SpineMark state={HEAD_STATE[head.tone] ?? 'unchecked'} className="mt-2" />
        <div className="flex-1">
          <h1 className="t-display text-ink">{head.title}</h1>
          <p className="t-body mt-2 max-w-3xl text-muted">{head.supporting}</p>
        </div>
        {/* Secondary by design. The unit stays lower-case: `.t-micro` upper-cases
            labels, which would render "0.1s" as "0.1S". */}
        <div className="t-micro shrink-0 space-y-0.5 text-right text-unknown">
          <div style={{ textTransform: 'none' }}>
            {(result.processing_ms / 1000).toFixed(1)}s
          </div>
          <div>
            {result.findings.length} {result.findings.length === 1 ? 'finding' : 'findings'}
          </div>
        </div>
      </div>
    </section>
  )
}

/**
 * The highest-consequence gap in the product, stated before anything else.
 *
 * Not coloured as a discrepancy: nothing is wrong with the bill. It is coloured
 * as unknown, because that is what it is — and it is impossible to miss,
 * because the alternative is a reviewer signing off medicines nobody looked at.
 */
function DocumentGaps({ result }: { result: ReconciliationResult }) {
  const gaps = documentGaps(result)
  if (gaps.length === 0) return null
  return (
    <section className="space-y-3 rounded border-2 border-dashed border-unknown bg-ink-50 px-6 py-5">
      {gaps.map((gap) => (
        <div key={gap.title} className="flex items-start gap-4">
          <SpineMark state="unchecked" className="mt-1.5" />
          <div>
            <h2 className="t-title text-ink">{gap.title}</h2>
            <p className="t-body mt-1 max-w-3xl text-muted">{gap.detail}</p>
          </div>
        </div>
      ))}
    </section>
  )
}

function FindingRow({
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
  const state: SpineState =
    finding.severity === 'critical' ? 'problem' : finding.severity === 'warning' ? 'warning' : 'unchecked'
  return (
    <li className="border-b border-ink-200 last:border-b-0">
      <button
        type="button"
        onClick={() => {
          setOpen(!open)
          setLocated(onLocate(finding))
        }}
        aria-expanded={open}
        className="flex w-full items-start gap-3 py-3 text-left hover:bg-paper"
      >
        <SpineMark state={state} className="mt-1" />
        <span className="t-body flex-1 text-ink">{text}</span>
        {technical ? <span className="t-data text-unknown">{finding.rule_code}</span> : null}
        <span className="t-data shrink-0 text-unknown">{open ? '−' : '+'}</span>
      </button>
      {open ? (
        <div className="pb-3 pl-6">
          {located === 'not-located' ? (
            <p className="t-small mb-2 text-muted">
              This line could not be located on the image, so there is nothing to highlight.
              Read it off the page before acting on this.
            </p>
          ) : null}
          {located === 'no-ref' ? (
            <p className="t-small mb-2 text-muted">
              This describes the document as a whole, not one line, so there is no region to
              highlight.
            </p>
          ) : null}
          <dl className="grid gap-x-6 gap-y-1 sm:grid-cols-[11rem_1fr]">
            {Object.entries(finding.detail).map(([key, value]) => (
              <div key={key} className="contents">
                <dt className="t-micro text-muted">{key}</dt>
                <dd className="t-data break-all text-ink">
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
  scanId = null,
}: {
  result: ReconciliationResult
  prescriptionImage: string | null
  billImage: string | null
  onReset: () => void
  /** True when reopened from history: a record of what was reported, not a new run. */
  readOnly?: boolean
  /** The stored record exports are built from. Null until the save completes. */
  scanId?: number | null
}) {
  const [technical, setTechnical] = useState(false)
  const [highlight, setHighlight] = useState<{ side: DocSide; itemId: string } | null>(null)
  const grouped = groupFindings(result.findings)
  const say = (f: Finding) => phrase(f, result.prescription, result.bill)

  const itemHasBox = useCallback(
    (side: DocSide, itemId: string): boolean => {
      const doc = side === 'prescription' ? result.prescription : result.bill
      const lines = [...doc.items, ...(doc.tests ?? [])]
      return lines.find((line) => line.item_id === itemId)?.bbox != null
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

  const analysis = [...grouped.discrepancies, ...grouped.noted]
  const unverified = grouped.unverified.length
  const notRun = grouped.notRun.length

  return (
    <div className="space-y-10">
      {readOnly ? (
        <p className="t-small rounded bg-ink-100 px-4 py-2.5 text-muted">
          Reopened from history. This is the result exactly as it was reported at the time.
        </p>
      ) : null}

      {/* 1 — SUMMARY */}
      <DocumentGaps result={result} />
      <Summary result={result} grouped={grouped} />

      {/* 2 — ANALYSIS */}
      {analysis.length > 0 ? (
        <Section title="Analysis">
          <ul className="rounded border border-ink-200 bg-surface px-5">
            {analysis.map((finding, index) => (
              <FindingRow
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

      {/* Neither of these may be deleted, and neither may be coloured as a
          finding. One compact line each, expandable. */}
      {unverified + notRun > 0 ? (
        <div className="space-y-2.5 rounded border border-ink-200 bg-paper px-5 py-4">
          {unverified > 0 ? (
            <Disclosure
              summary={`${unverified} ${unverified === 1 ? 'value' : 'values'} could not be verified`}
            >
              <p className="t-small mb-2 max-w-3xl text-muted">
                These were checked and could not be concluded either way. No discrepancy is
                claimed.
              </p>
              <ul className="space-y-1">
                {grouped.unverified.map((finding, index) => (
                  <li key={`uv-${index}`} className="t-small text-muted">
                    {say(finding)}
                    {technical ? (
                      <span className="t-data ml-2 text-unknown">
                        {String(finding.detail['basis_method'] ?? finding.rule_code)}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </Disclosure>
          ) : null}
          {notRun > 0 ? (
            <Disclosure summary={`${notRun} ${notRun === 1 ? 'check' : 'checks'} could not run`}>
              <p className="t-small mb-2 max-w-3xl text-muted">
                These were not performed, because the documents did not carry the values they
                need. They are not passes.
              </p>
              <ul className="space-y-1">
                {grouped.notRun.map((finding, index) => (
                  <li key={`nr-${index}`} className="t-small text-muted">
                    {say(finding)}
                  </li>
                ))}
              </ul>
            </Disclosure>
          ) : null}
        </div>
      ) : null}

      <Section title="Reimbursement">
        <Reimbursement summary={result.reimbursement} />
      </Section>

      {/* 3 — TABLES */}
      <Section title="Medicines">
        <div className="rounded border border-ink-200 bg-surface px-5 py-4">
          <MedicinesTable result={result} onHover={hoverRow} technical={technical} />
        </div>
      </Section>

      <Section title="Lab tests">
        <div className="rounded border border-ink-200 bg-surface px-5 py-4">
          <LabTestsTable result={result} onHover={hoverRow} technical={technical} />
        </div>
      </Section>

      <div className="flex flex-wrap items-center gap-4 border-t border-ink-200 pt-6">
        <button
          type="button"
          onClick={onReset}
          className="t-small rounded bg-seal px-5 py-2.5 font-semibold text-white hover:opacity-90"
        >
          {readOnly ? 'Back to a new reconciliation' : 'Reconcile another'}
        </button>
        <ExportBar scanId={scanId} />
        <label className="t-small flex cursor-pointer items-center gap-2 text-muted">
          <input
            type="checkbox"
            checked={technical}
            onChange={(event) => setTechnical(event.target.checked)}
            className="h-3.5 w-3.5 accent-[color:var(--color-seal)]"
          />
          Technical details
        </label>
      </div>

      {/* Nothing is removed from the product. It moves behind one switch. */}
      {technical ? (
        <Section title="Technical details">
          <div className="space-y-6">
            <dl className="grid gap-4 rounded border border-ink-200 bg-surface px-5 py-4 sm:grid-cols-4">
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
                ['Test pairs', String((result.matched_tests ?? []).length)],
                [
                  'Investigations section',
                  result.prescription.investigations_present == null
                    ? 'could not tell'
                    : String(result.prescription.investigations_present),
                ],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt className="t-micro text-muted">{label}</dt>
                  <dd className="t-data mt-1 text-ink">{value}</dd>
                </div>
              ))}
            </dl>

            {grouped.quality.length > 0 ? (
              <Disclosure summary={`Extraction quality · ${grouped.quality.length}`}>
                <ul className="space-y-1">
                  {grouped.quality.map((finding, index) => (
                    <li key={`q-${index}`} className="t-small text-muted">
                      <span className="t-data text-unknown">{finding.rule_code}</span>{' '}
                      {finding.message}
                    </li>
                  ))}
                </ul>
              </Disclosure>
            ) : null}

            <Disclosure summary="Raw response">
              <pre className="t-data max-h-[28rem] overflow-auto rounded border border-ink-200 bg-surface p-4 text-ink">
                {JSON.stringify(result, null, 2)}
              </pre>
            </Disclosure>

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
