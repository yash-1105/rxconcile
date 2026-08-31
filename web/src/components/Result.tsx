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

import { useCallback, useEffect, useMemo, useState } from 'react'
import { documentGaps, groupFindings, headline, phrase } from '../lib/phrasing'
import {
  criticalCount,
  discrepancyCount,
  groupByItem,
  hideNonMedicineNoise,
  type FindingGroup,
} from '../lib/grouping'
import type { DocSide, Finding, ReconciliationResult } from '../types/api'
import { AuditPanel } from './Audit'
import { UNCHECKED_CODES } from '../lib/rowStatus'
import { STATUS_LABEL } from '../lib/spineStatus'
import { SpineLegend, SpineMark, type SpineState } from './Spine'
import { ExportBar } from './Export'
import { BulkDecisions, LabTestsTable, MedicinesTable, TableFilter } from './Tables'
import { ExcludedLine, SummaryPanel } from './SummaryPanel'
import {
  claimTotal,
  countRows,
  decisionsFor,
  medicineRowsOf,
  testRowsOf,
  type Decisions,
  type RowFilter,
} from '../lib/rows'
import { fetchAllowance, saveDecisions } from '../api/client'
import type { AllowanceView } from '../types/api'

/** Whether a finding's source line could be pointed at on the image. */
export type LocateResult = 'located' | 'not-located' | 'no-ref'

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
        <span className="t-small w-3 shrink-0 text-unknown">{open ? '−' : '+'}</span>
        <span className="t-small">{summary}</span>
      </button>
      {open ? <div className="mt-3 pl-5">{children}</div> : null}
    </div>
  )
}

function FindingRow({
  group,
  text,
  onLocate,
  technical,
}: {
  group: FindingGroup
  text: string
  onLocate: (finding: Finding) => LocateResult
  technical: boolean
}) {
  const [open, setOpen] = useState(false)
  const [located, setLocated] = useState<LocateResult | null>(null)
  const { headline: lead, findings, severity } = group
  const extra = findings.length - 1
  // An info finding is not automatically "not checked". A brand substitution
  // WAS checked and matched; only an unverifiable or unrun check is unchecked.
  // The legend states what each mark means, so a wrong mark is now a stated
  // falsehood rather than an ambiguity.
  const state: SpineState =
    severity === 'critical'
      ? 'problem'
      : severity === 'warning'
        ? 'warning'
        : findings.some((f) => UNCHECKED_CODES.has(f.rule_code))
          ? 'unchecked'
          : 'clean'
  return (
    <li className="border-b border-ink-200 last:border-b-0">
      <button
        type="button"
        onClick={() => {
          setOpen(!open)
          setLocated(onLocate(lead))
        }}
        aria-expanded={open}
        className="flex w-full items-start gap-3 py-3 text-left hover:bg-paper"
      >
        <SpineMark state={state} className="mt-1" />
        <span className="t-micro mt-0.5 w-[5.5rem] shrink-0 text-muted">
          {STATUS_LABEL[state]}
        </span>
        <span className="t-body flex-1 text-ink">
          {text}
          {extra > 0 ? (
            <span className="t-small ml-2 text-muted">(+{extra} more)</span>
          ) : null}
        </span>
        {technical ? <span className="t-small text-unknown">{lead.rule_code}</span> : null}
        <span className="t-small shrink-0 text-unknown">{open ? '−' : '+'}</span>
      </button>
      {open ? (
        <div className="space-y-4 pb-3 pl-6">
          {located === 'not-located' ? (
            <p className="t-small text-muted">
              This line could not be located on the image, so there is nothing to highlight.
              Read it off the page before acting on this.
            </p>
          ) : null}
          {located === 'no-ref' ? (
            <p className="t-small text-muted">
              This describes the document as a whole, not one line, so there is no region to
              highlight.
            </p>
          ) : null}
          {/* Every finding in the group, in full. Nothing is lost by grouping;
              it moves one click away. */}
          {findings.map((finding, index) => (
            <div key={`${finding.rule_code}-${index}`}>
              <p className="t-small text-ink">
                <span className="t-small mr-2 text-unknown">{finding.rule_code}</span>
                {finding.message}
              </p>
              {Object.keys(finding.detail).length > 0 ? (
                <dl className="mt-1 grid gap-x-6 gap-y-1 sm:grid-cols-[11rem_1fr]">
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
              ) : null}
            </div>
          ))}
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
  employeeNumber = '',
  storedDecisions,
}: {
  result: ReconciliationResult
  prescriptionImage: string | null
  billImage: string | null
  onReset: () => void
  /** True when reopened from history: a record of what was reported, not a new run. */
  readOnly?: boolean
  /** The stored record exports are built from. Null until the save completes. */
  scanId?: number | null
  /** Whose allowance this claim draws against. */
  employeeNumber?: string
  /** Decisions as they were last saved. Empty for a scan being run now. */
  storedDecisions?: Decisions
}) {
  const [technical, setTechnical] = useState(false)
  const [filters, setFilters] = useState<{ medicines: RowFilter; tests: RowFilter }>({
    medicines: 'all',
    tests: 'all',
  })
  const allRows = useMemo(
    () => [...medicineRowsOf(result), ...testRowsOf(result)],
    [result],
  )
  // Seeded from what was saved, falling back to the rows themselves: matched
  // lines start accepted, anything with a problem starts undecided.
  const [decisions, setDecisions] = useState<Decisions>(() =>
    decisionsFor(allRows, storedDecisions),
  )
  // Reseeded during render rather than in an effect, so the first save after
  // opening a different scan cannot write this scan's decisions onto it.
  const [seededFor, setSeededFor] = useState(result)
  if (seededFor !== result) {
    setSeededFor(result)
    setDecisions(decisionsFor(allRows, storedDecisions))
  }

  const [allowance, setAllowance] = useState<AllowanceView | null>(null)
  useEffect(() => {
    if (!employeeNumber) return
    fetchAllowance(employeeNumber, scanId)
      .then(setAllowance)
      .catch(() => setAllowance(null))
  }, [employeeNumber, scanId])

  // The claim reads the same rows the tables render, so the figure on screen
  // and the figure sent to the server cannot disagree.
  const claim = claimTotal(allRows, decisions)

  const decide = (key: string, decision: 'accept' | 'reject' | 'unset', remark?: string) =>
    setDecisions((current) => ({ ...current, [key]: { decision, remark } }))

  const decideAll = (rows: typeof allRows, decision: 'accept' | 'reject') =>
    setDecisions((current) => {
      const next = { ...current }
      for (const row of rows) next[row.key] = { decision, remark: current[row.key]?.remark }
      return next
    })

  // Persisted after the reviewer stops changing their mind, not on every click.
  useEffect(() => {
    if (scanId === null || scanId === undefined) return
    const timer = setTimeout(() => {
      void saveDecisions(scanId, decisions, claim).catch(() => undefined)
    }, 600)
    return () => clearTimeout(timer)
  }, [scanId, decisions, claim])
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

  // One row per item. Two findings about Alprax are one row about Alprax, and
  // the summary counts items with problems rather than raw findings.
  //
  // Non-medicine noise is filtered from the DEFAULT view only. Technical
  // details shows everything, and the header count follows whichever list is
  // on screen so the two always agree.
  // Checks about the DOCUMENT rather than a line. They attach to no billed
  // line, so no table row can carry them, and they are listed with everything
  // else rather than in a panel of their own. They are NOT dropped: a check
  // that could not run is not a check that passed, and one that silently
  // vanishes is the exact failure this project keeps fixing.
  const documentChecks = grouped.notRun.filter(
    (f) => f.prescribed_ref === null && f.billed_ref === null,
  )
  const visible = technical
    ? [...grouped.discrepancies, ...grouped.noted, ...documentChecks]
    : [...hideNonMedicineNoise([...grouped.discrepancies, ...grouped.noted]), ...documentChecks]
  const analysis = groupByItem(visible, result)
  const medicineCounts = countRows(medicineRowsOf(result))
  const testCounts = countRows(testRowsOf(result))
  const affectedItems = discrepancyCount(analysis)
  const seriousItems = criticalCount(analysis)
  // Counted from the reimbursement assessment rather than from finding codes:
  // that is where a reader can now see the reason for each one.
  const manualChecks = result.reimbursement?.needs_review_line_count ?? 0

  return (
    <div className="space-y-10">
      {readOnly ? (
        <p className="t-small rounded bg-ink-100 px-4 py-2.5 text-muted">
          Reopened from history. This is the result exactly as it was reported at the time.
        </p>
      ) : null}

      {/* 1 — SUMMARY. One panel: the documents on the left, the money on the
          right. What is excluded sits under it as a line, not as cards. */}
      <div>
        <SummaryPanel
          gaps={documentGaps(result)}
          head={headline(result, grouped, affectedItems, seriousItems)}
          submission={result.submission}
          manualChecks={manualChecks}
          medicines={medicineCounts}
          tests={testCounts}
          filters={filters}
          onPick={(table, filter) => setFilters((f) => ({ ...f, [table]: filter }))}
          allowance={allowance}
          claim={claim}
          currency={result.reimbursement?.currency ?? 'INR'}
          /* Never on a record reopened from history: an intro animation on an
             old result implies something just happened. */
          animate={!readOnly}
        />
        <ExcludedLine
          currency={result.reimbursement?.currency ?? 'INR'}
          notOnPrescription={{
            total: result.reimbursement?.not_eligible_total ?? '0',
            lines: result.reimbursement?.not_eligible_line_count ?? 0,
          }}
          notMedicine={{
            total: result.reimbursement?.non_medicine_total ?? '0',
            lines: result.reimbursement?.non_medicine_line_count ?? 0,
          }}
        />
      </div>

      {/* 2 — ANALYSIS, folded away. The tiles and tables answer the question;
          this is for the reader who wants the reasoning. */}
      {analysis.length > 0 ? (
        <Section title="Analysis">
          <Disclosure summary="See the detailed analysis">
            <div className="mb-3 flex justify-end">
              <SpineLegend />
            </div>
          <ul className="rounded border border-ink-200 bg-surface px-5">
            {analysis.map((group) => (
              <FindingRow
                key={group.key}
                group={group}
                text={say(group.headline)}
                onLocate={locate}
                technical={technical}
              />
            ))}
          </ul>
          </Disclosure>
        </Section>
      ) : null}

      {/* 3 — TABLES */}
      <Section title="Medicines">
        <div className="mb-3 flex justify-end">
          <TableFilter
            label="Show"
            value={filters.medicines}
            onChange={(next) => setFilters((f) => ({ ...f, medicines: next }))}
          />
        </div>
        <div className="overflow-hidden rounded border border-ink-200 bg-surface">
          {/* The table fits without scrolling from 1280px up. Below that it
              scrolls inside its own card rather than being clipped — a column
              a phone cannot reach is worse than one it has to scroll to. */}
          <div className="overflow-x-auto">
            <MedicinesTable
              result={result}
              onHover={hoverRow}
              technical={technical}
              filter={filters.medicines}
              decisions={decisions}
              onDecision={decide}
            />
          </div>
          <BulkDecisions onAll={(d) => decideAll(medicineRowsOf(result), d)} />
        </div>
      </Section>

      <Section title="Lab tests">
        <div className="mb-3 flex justify-end">
          <TableFilter
            label="Show"
            value={filters.tests}
            onChange={(next) => setFilters((f) => ({ ...f, tests: next }))}
          />
        </div>
        <div className="overflow-hidden rounded border border-ink-200 bg-surface">
          <div className="overflow-x-auto">
            <LabTestsTable
              result={result}
              onHover={hoverRow}
              technical={technical}
              filter={filters.tests}
              decisions={decisions}
              onDecision={decide}
            />
          </div>
          <BulkDecisions onAll={(d) => decideAll(testRowsOf(result), d)} />
        </div>
      </Section>

      <div className="flex flex-wrap items-center gap-4 border-t border-ink-200 pt-6">
        <button
          type="button"
          onClick={onReset}
          className="t-small rounded bg-seal px-5 py-2.5 font-semibold text-white hover:opacity-90"
        >
          {readOnly ? 'Verify another' : 'Verify another'}
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
                      <span className="t-small text-unknown">{finding.rule_code}</span>{' '}
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
