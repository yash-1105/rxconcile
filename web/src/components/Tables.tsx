/**
 * The two comparison tables: medicines and lab tests.
 *
 * Each attribute splits into a prescribed column and a billed column under one
 * grouped heading, so the table reads as two halves rather than as eleven
 * unrelated columns. Rows with nothing wrong recede: the eye should land on the
 * flagged rows first.
 *
 * Presentation only. Every value shown is read from the response as computed —
 * nothing here derives a quantity, resolves a drug or decides a status. Where a
 * value is absent it renders as an em-dash, never as a zero and never as a
 * guess.
 */

import { remark, testRemark } from '../lib/phrasing'
import {
  applyFilter,
  medicineRowsOf,
  testRowsOf,
  ROW_FILTERS,
  type MedRow,
  type RowFilter,
  type TestRow,
} from '../lib/rows'
import type {
  BilledItem,
  CanonicalMatch,
  Finding,
  PrescribedItem,
  ReconciliationResult,
  Severity,
} from '../types/api'
import { STATUS_LABEL } from '../lib/spineStatus'
import { SpineMark, type SpineState } from './Spine'


/**
 * Row colour, alongside the mark.
 *
 * Confident tints so a reader can scan the table without reading it. The mark
 * and the status word carry the same meaning in shape and in text, so the table
 * survives being printed or read by someone who cannot separate the hues.
 */
const ROW_TINT: Record<SpineState, string> = {
  clean: 'bg-emerald-50/70',
  substitution: 'bg-amber-50/80',
  warning: 'bg-red-50/60',
  problem: 'bg-red-50/80',
  unchecked: 'bg-ink-100/70',
  'out-of-scope': 'bg-ink-100/70',
}

export function TableFilter({
  value,
  onChange,
  label,
}: {
  value: RowFilter
  onChange: (next: RowFilter) => void
  label: string
}) {
  return (
    <label className="flex items-center gap-2">
      <span className="t-micro text-muted">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value as RowFilter)}
        className="t-small rounded border border-ink-300 bg-surface px-2.5 py-1.5 text-ink"
      >
        {ROW_FILTERS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  )
}

function RowNumber({ index }: { index: number }) {
  return (
    <td className="t-small px-4 py-4 align-top text-muted tabular-nums">{index + 1}</td>
  )
}

function Val({ children, muted = false }: { children: React.ReactNode; muted?: boolean }) {
  const empty = children === null || children === undefined || children === ''
  if (empty) {
    return (
      <span className="t-data text-unknown" title="Not present on the document">
        —
      </span>
    )
  }
  return <span className={`t-data ${muted ? 'text-muted' : 'text-ink'}`}>{children}</span>
}

function strengthOf(item: PrescribedItem | BilledItem | null): string | null {
  if (!item || item.strength_value === null) return null
  return `${item.strength_value}${item.strength_unit ?? ''}`
}

/**
 * The quantity a course implies, as the ENGINE computed it.
 *
 * Deliberately not derived here. The expected number lives in the detail of
 * whichever quantity rule ran; if no quantity rule ran there is no expectation
 * to show, and the cell stays an em-dash. Recomputing it in the browser would
 * risk showing a number the engine never agreed with.
 */
function expectedQty(findings: Finding[]): string | null {
  for (const found of findings) {
    const value = found.detail['expected_units']
    if (typeof value === 'number') return String(value)
  }
  return null
}

function billedQty(item: BilledItem | null): string | null {
  if (!item || item.quantity === null) return null
  return item.pack_size ? `${item.quantity} · ${item.pack_size}` : String(item.quantity)
}

function saltOf(row: MedRow, canonical: Map<string, CanonicalMatch>): string | null {
  const resolved =
    canonical.get(row.prescribed?.item_id ?? '')?.salt ??
    canonical.get(row.billed?.item_id ?? '')?.salt
  return resolved ?? row.prescribed?.salt ?? row.billed?.salt ?? null
}

/**
 * Which cells a finding points at, and how loudly.
 *
 * Severity comes from the engine, so the existing discipline holds without
 * restating it: red only for criticals, amber only for warnings, grey for
 * anything unverifiable. Nothing unverifiable is ever painted as a finding.
 */
const FIELD_OF: Record<string, 'drug' | 'strength' | 'form' | 'qty'> = {
  STRENGTH_MISMATCH: 'strength',
  STRENGTH_UNIT_UNSTATED: 'strength',
  FORM_MISMATCH: 'form',
  QUANTITY_SHORT: 'qty',
  QUANTITY_EXCESS: 'qty',
  QUANTITY_AMBIGUOUS: 'qty',
  BRAND_SUBSTITUTION: 'drug',
  SALT_DIFFERENT_CLASS: 'drug',
  DUPLICATE_THERAPY: 'drug',
  SCHEDULE_H_UNBACKED: 'drug',
}

const MARK_CLASS: Record<Severity, string> = {
  critical: 'bg-flag/10 ring-1 ring-flag/40',
  warning: 'bg-caution/10 ring-1 ring-caution/40',
  info: 'bg-ink-100',
}

/** The loudest marking any finding puts on one field of a row. */
function marksFor(findings: Finding[]): Partial<Record<'drug' | 'strength' | 'form' | 'qty', Severity>> {
  const rank: Record<Severity, number> = { critical: 0, warning: 1, info: 2 }
  const out: Partial<Record<'drug' | 'strength' | 'form' | 'qty', Severity>> = {}
  for (const found of findings) {
    const field = FIELD_OF[found.rule_code]
    if (!field) continue
    const current = out[field]
    if (current === undefined || rank[found.severity] < rank[current]) {
      out[field] = found.severity
    }
  }
  return out
}

function mark(severity: Severity | undefined): string {
  return severity ? `rounded px-1.5 ${MARK_CLASS[severity]}` : ''
}

function GroupHead({
  label,
  span = 2,
}: {
  label: string
  span?: number
}) {
  return (
    <th
      colSpan={span}
      scope="colgroup"
      className="t-micro border-b border-l border-ink-200 px-4 pt-3 pb-1.5 text-center text-muted first:border-l-0"
    >
      {label}
    </th>
  )
}

function SubHead({ label, side }: { label: string; side?: 'rx' | 'bill' }) {
  return (
    <th
      scope="col"
      className={`t-micro px-3 pb-2 text-left font-normal text-muted ${
        side === 'rx' ? 'border-l border-ink-200' : ''
      }`}
    >
      {label}
    </th>
  )
}

function StatusCell({ status, partial }: { status: SpineState; partial: boolean }) {
  return (
    <td className="px-4 py-4 align-top whitespace-nowrap">
      <span className="inline-flex items-center gap-2">
        <SpineMark state={status} />
        <span className="t-micro text-muted">{STATUS_LABEL[status]}</span>
        {partial ? (
          <span
            className="t-micro text-unknown"
            title="One check on this line could not be concluded. It is not a discrepancy."
            aria-label="one check could not be concluded"
          >
            *
          </span>
        ) : null}
      </span>
    </td>
  )
}

/**
 * Remark sits second, immediately after status.
 *
 * It carries the summary of the row, so it is read first. It used to be pinned
 * to the right edge to stop it scrolling away; leading the row solves that
 * outright and the pin is gone.
 */
function RemarkCell({ text }: { text: string }) {
  return (
    <td className="min-w-[14rem] border-l border-ink-200 px-4 py-4 align-top">
      {text ? (
        <span className="t-small text-ink">{text}</span>
      ) : (
        <span className="t-data text-unknown">—</span>
      )}
    </td>
  )
}

export function MedicinesTable({
  result,
  onHover,
  technical = false,
  filter = 'all',
}: {
  result: ReconciliationResult
  onHover?: (row: { prescribedId: string | null; billedId: string | null } | null) => void
  technical?: boolean
  filter?: RowFilter
}) {
  const rows = applyFilter(medicineRowsOf(result), filter)
  const canonical = new Map((result.canonical ?? []).map((c) => [c.item_id, c]))
  if (rows.length === 0) {
    return (
      <p className="t-small text-muted">
        {filter === 'all'
          ? 'Neither document carries a medicine line. Nothing to compare here.'
          : 'No lines match this filter.'}
      </p>
    )
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[54rem] border-collapse">
        <thead>
          <tr>
            <th rowSpan={2} scope="col" className="t-micro px-4 pb-3 text-left text-muted">
              #
            </th>
            <th rowSpan={2} scope="col" className="t-micro px-4 pb-3 text-left text-muted">
              Status
            </th>
            {/* Remark leads: it carries the summary and is what a reviewer
                reads first. */}
            <th
              rowSpan={2}
              scope="col"
              className="t-micro min-w-[14rem] border-l border-ink-200 px-4 pb-3 text-left text-muted"
            >
              Remark
            </th>
            <GroupHead label="Drug" />
            <th
              rowSpan={2}
              scope="col"
              className="t-micro max-w-[13rem] border-l border-ink-200 px-4 pb-3 text-left text-muted"
            >
              Salt
            </th>
            <GroupHead label="Strength" />
            <GroupHead label="Form" />
            <GroupHead label="Quantity" />
            {technical ? <GroupHead label="Ids" /> : null}
          </tr>
          <tr className="border-b border-ink-200">
            <SubHead label="Prescribed" side="rx" />
            <SubHead label="Billed" />
            <SubHead label="Prescribed" side="rx" />
            <SubHead label="Billed" />
            <SubHead label="Prescribed" side="rx" />
            <SubHead label="Billed" />
            <SubHead label="Prescribed" side="rx" />
            <SubHead label="Billed" />
            {technical ? (
              <>
                <SubHead label="Rx" side="rx" />
                <SubHead label="Bill" />
              </>
            ) : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const quiet = row.status === 'clean'
            const m = marksFor(row.findings)
            // Only mark a pair when both halves exist: on an unmatched line the
            // row status already says everything, and painting a lone cell
            // would imply a comparison that never happened.
            const pair = row.prescribed !== null && row.billed !== null
            const at = (field: 'drug' | 'strength' | 'form' | 'qty') =>
              pair ? mark(m[field]) : ''
            return (
              <tr
                key={row.key}
                onMouseEnter={() =>
                  onHover?.({
                    prescribedId: row.prescribed?.item_id ?? null,
                    billedId: row.billed?.item_id ?? null,
                  })
                }
                onMouseLeave={() => onHover?.(null)}
                className={`border-b border-ink-200 align-top ${ROW_TINT[row.status]}`}
              >
                <RowNumber index={index} />
                <StatusCell status={row.status} partial={row.partial} />
                <RemarkCell text={remark(row.codes, row.findings)} />
                <td className="border-l border-ink-200 px-4 py-4">
                  <span className={at('drug')}>
                    <Val muted={quiet}>{row.prescribed?.drug_name}</Val>
                  </span>
                </td>
                <td className="px-4 py-4">
                  <span className={at('drug')}>
                    <Val muted={quiet}>{row.billed?.drug_name}</Val>
                  </span>
                </td>
                <td className="max-w-[13rem] border-l border-ink-200 px-4 py-4 break-words">
                  <Val muted>{saltOf(row, canonical)}</Val>
                </td>
                <td className="border-l border-ink-200 px-4 py-4">
                  <span className={at('strength')}>
                    <Val muted={quiet}>{strengthOf(row.prescribed)}</Val>
                  </span>
                </td>
                <td className="px-4 py-4">
                  <span className={at('strength')}>
                    <Val muted={quiet}>{strengthOf(row.billed)}</Val>
                  </span>
                </td>
                <td className="border-l border-ink-200 px-4 py-4">
                  <span className={at('form')}>
                    <Val muted={quiet}>{row.prescribed?.form}</Val>
                  </span>
                </td>
                <td className="px-4 py-4">
                  <span className={at('form')}>
                    <Val muted={quiet}>{row.billed?.form}</Val>
                  </span>
                </td>
                <td className="border-l border-ink-200 px-4 py-4">
                  <span className={at('qty')}>
                    <Val muted={quiet}>{expectedQty(row.findings)}</Val>
                  </span>
                </td>
                <td className="px-4 py-4">
                  <span className={at('qty')}>
                    <Val muted={quiet}>{billedQty(row.billed)}</Val>
                  </span>
                </td>
                {technical ? (
                  <>
                    <td className="border-l border-ink-200 px-4 py-4">
                      <span className="t-data text-muted" title={row.prescribed?.raw_text}>
                        {row.prescribed?.item_id ?? '—'}
                      </span>
                    </td>
                    <td className="px-4 py-4">
                      <span className="t-data text-muted" title={row.billed?.raw_text}>
                        {row.billed?.item_id ?? '—'}
                        {row.similarity !== null ? ` · ${row.similarity.toFixed(2)}` : ''}
                      </span>
                    </td>
                  </>
                ) : null}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function panelOf(row: TestRow): string | null {
  for (const found of row.findings) {
    const value = found.detail['panel'] ?? found.detail['resolved_as']
    if (typeof value === 'string' && value) return value
  }
  return row.prescribed?.panel ?? row.billed?.panel ?? null
}

export function LabTestsTable({
  result,
  onHover,
  technical = false,
  filter = 'all',
}: {
  result: ReconciliationResult
  onHover?: (row: { prescribedId: string | null; billedId: string | null } | null) => void
  technical?: boolean
  filter?: RowFilter
}) {
  const all = testRowsOf(result)
  const rows = applyFilter(all, filter)
  const coveredCount =
    (result.matched_tests ?? []).length === 1
      ? all.filter((r) => r.prescribed === null && r.billed !== null && r.findings.length === 0)
          .length
      : 0

  if (all.length > 0 && rows.length === 0) {
    return <p className="t-small text-muted">No lines match this filter.</p>
  }

  // An empty table reads as a rendering failure or a missed section. These two
  // states are entirely different results and must never render alike.
  if (all.length === 0) {
    // `undefined` on a legacy record is not the same as a measured `null`, and
    // neither may render as "no tests ordered".
    const present = result.prescription.investigations_present ?? null
    if (result.prescription.tests === undefined) {
      return (
        <p className="t-body text-muted">
          This result was recorded before lab tests were reconciled, so it carries no
          investigations data. Nothing here says tests were or were not ordered.
        </p>
      )
    }
    if (present === true) {
      return (
        <p className="t-body text-ink">
          <strong className="font-semibold">
            The investigations section could not be read.
          </strong>{' '}
          This prescription orders lab work, but no test line could be made out. This is not a
          finding that no tests were ordered — it is a finding that what was ordered is unknown.
        </p>
      )
    }
    if (present === false) {
      return (
        <p className="t-body text-muted">
          No investigations ordered on this prescription. Nothing to compare, and nothing
          missing.
        </p>
      )
    }
    return (
      <p className="t-body text-ink">
        No investigations section was found on this prescription, but its presence could not be
        confirmed. Read the page before treating this as "no tests ordered".
      </p>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] border-collapse">
        <thead>
          <tr>
            <th rowSpan={2} scope="col" className="t-micro px-4 pb-3 text-left text-muted">
              #
            </th>
            <th rowSpan={2} scope="col" className="t-micro px-4 pb-3 text-left text-muted">
              Status
            </th>
            <th
              rowSpan={2}
              scope="col"
              className="t-micro min-w-[14rem] border-l border-ink-200 px-4 pb-3 text-left text-muted"
            >
              Remark
            </th>
            <GroupHead label="Test" />
            <th rowSpan={2} scope="col" className="t-micro border-l border-ink-200 px-4 pb-3 text-left text-muted">
              Panel
            </th>
            {technical ? <GroupHead label="Ids" /> : null}
          </tr>
          <tr className="border-b border-ink-200">
            <SubHead label="Prescribed" side="rx" />
            <SubHead label="Billed" />
            {technical ? (
              <>
                <SubHead label="Rx" side="rx" />
                <SubHead label="Bill" />
              </>
            ) : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const quiet = row.status === 'clean'
            return (
              <tr
                key={row.key}
                onMouseEnter={() =>
                  onHover?.({
                    prescribedId: row.prescribed?.item_id ?? null,
                    billedId: row.billed?.item_id ?? null,
                  })
                }
                onMouseLeave={() => onHover?.(null)}
                className={`border-b border-ink-200 align-top ${ROW_TINT[row.status]}`}
              >
                <RowNumber index={index} />
                <StatusCell status={row.status} partial={row.partial} />
                <RemarkCell
                  text={
                    row.findings.length === 0 && row.prescribed === null && row.billed !== null
                      ? 'Billed as part of an ordered panel'
                      : row.findings.length === 0 && row.prescribed !== null && coveredCount > 0
                        ? `Ordered as a panel — billed as ${coveredCount + 1} itemised lines`
                        : testRemark(row.codes, row.findings)
                  }
                />
                <td className="border-l border-ink-200 px-4 py-4">
                  <Val muted={quiet}>{row.prescribed?.test_name}</Val>
                </td>
                <td className="px-4 py-4">
                  <Val muted={quiet}>{row.billed?.test_name}</Val>
                </td>
                <td className="border-l border-ink-200 px-4 py-4">
                  <Val muted>{panelOf(row)}</Val>
                </td>
                {technical ? (
                  <>
                    <td className="border-l border-ink-200 px-4 py-4">
                      <span className="t-data text-muted" title={row.prescribed?.raw_text}>
                        {row.prescribed?.item_id ?? '—'}
                      </span>
                    </td>
                    <td className="px-4 py-4">
                      <span className="t-data text-muted" title={row.billed?.raw_text}>
                        {row.billed?.item_id ?? '—'}
                      </span>
                    </td>
                  </>
                ) : null}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
