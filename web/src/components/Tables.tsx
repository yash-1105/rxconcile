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

import { useLayoutEffect, useRef, useState } from 'react'
import { remark, testRemark } from '../lib/phrasing'
import {
  applyFilter,
  defaultDecision,
  isClaimable,
  type Decisions,
  medicineRowsOf,
  testRowsOf,
  ROW_FILTERS,
  type MedRow,
  type RowFilter,
  type TestRow,
  testLabel,
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
  clean: 'bg-tint-clean',
  substitution: 'bg-tint-substitution',
  warning: 'bg-tint-warning',
  problem: 'bg-tint-problem',
  unchecked: 'bg-tint-neutral',
  'out-of-scope': 'bg-tint-neutral',
}

/**
 * Aligning the decision panel with the table it sits beside.
 *
 * BOTH sides derive their height from one measured source: the larger of the
 * row's natural height and its control's natural height. Neither can outgrow
 * the other, because neither decides alone.
 *
 * The previous version guessed what a control needed — a floor of 68px, or 104
 * once a rejection reason was open. It did not account for the "Not claimable"
 * and "Undecided" lines beneath the buttons, so those entries were given less
 * room than they took and spilled across the row boundary below.
 *
 * The measurement is not circular. A control's natural height is read from an
 * INNER element that is never given a height, so it depends only on what the
 * control contains. That figure is applied to the row, where CSS treats a
 * `height` on a `tr` as a minimum — so the row renders at the larger of the
 * two. The rendered row height is then applied back to the control's outer box.
 * The loop terminates because the inner element never changes size in response
 * to either.
 */
interface Alignment {
  /** True while the panel is beside the table rather than stacked under it. */
  aligned: boolean
  /** Height of the table head, so the panel's own head lines up with it. */
  head: number
  /** What each control needs, by row key. Applied to the ROW as a minimum. */
  needs: Record<string, number>
  /** What each row ended up at. Applied to the CONTROL as its exact height. */
  rows: Record<string, number>
}

const UNALIGNED: Alignment = { aligned: false, head: 0, needs: {}, rows: {} }

function sameMap(a: Record<string, number>, b: Record<string, number>): boolean {
  const keys = Object.keys(b)
  if (Object.keys(a).length !== keys.length) return false
  return keys.every((key) => Math.abs((a[key] ?? -1) - (b[key] ?? -1)) <= 0.5)
}

function sameAlignment(a: Alignment, b: Alignment): boolean {
  return (
    a.aligned === b.aligned &&
    Math.abs(a.head - b.head) <= 0.5 &&
    sameMap(a.needs, b.needs) &&
    sameMap(a.rows, b.rows)
  )
}

function useRowAlignment(): {
  ref: React.RefObject<HTMLDivElement | null>
  alignment: Alignment
} {
  const ref = useRef<HTMLDivElement>(null)
  const [alignment, setAlignment] = useState<Alignment>(UNALIGNED)

  useLayoutEffect(() => {
    const node = ref.current
    if (node === null) return
    const measure = () => {
      const table = node.querySelector('table')
      if (table === null) return
      // Below `lg` the panel sits UNDER the table rather than beside it, where
      // matching heights would only add empty space. Both take their natural
      // height and each control names the row it belongs to instead.
      if (!window.matchMedia('(min-width: 1024px)').matches) {
        // eslint-disable-next-line react/set-state-in-effect
        setAlignment((current) => (current.aligned ? UNALIGNED : current))
        return
      }
      const needs: Record<string, number> = {}
      for (const inner of node.querySelectorAll('[data-decision-for]')) {
        const key = inner.getAttribute('data-decision-for')
        if (key !== null) needs[key] = inner.getBoundingClientRect().height
      }
      const rows: Record<string, number> = {}
      for (const row of table.querySelectorAll('tbody tr[data-row-key]')) {
        const key = row.getAttribute('data-row-key')
        if (key !== null) rows[key] = row.getBoundingClientRect().height
      }
      const head = table.querySelector('thead')?.getBoundingClientRect().height ?? 0
      const next: Alignment = { aligned: true, head, needs, rows }
      // Guarded, or the observer's own callback would re-render for ever.
      // eslint-disable-next-line react/set-state-in-effect
      setAlignment((current) => (sameAlignment(current, next) ? current : next))
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    for (const row of node.querySelectorAll('tbody tr')) observer.observe(row)
    for (const inner of node.querySelectorAll('[data-decision-for]')) observer.observe(inner)
    return () => observer.disconnect()
  })

  return { ref, alignment }
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

/**
 * Accept or reject, beside the table rather than inside it.
 *
 * It used to be the last column, which put the only action on the page past the
 * right edge of a table that scrolls. Out here it stays put while the table
 * scrolls underneath, and each entry is sized to the row it belongs to.
 *
 * Every row gets a control so a reviewer can annotate anything, but only a
 * CLAIMABLE line moves the total — accepting a delivery charge does not make it
 * reimbursable, and the label says so rather than letting the control imply it.
 */
function DecisionEntry({
  row,
  index,
  decisions,
  onChange,
  height,
}: {
  row: { key: string; status: SpineState; billed: unknown; prescribed: unknown; findings: unknown[] }
  index: number
  decisions: Decisions
  onChange: (key: string, decision: 'accept' | 'reject' | 'unset', remark?: string) => void
  height: number | undefined
}) {
  const claimable = isClaimable(row as never)
  const current = decisions[row.key]?.decision ?? defaultDecision(row as never)
  const remarkText = decisions[row.key]?.remark ?? ''
  return (
    <div
      // Carries its row's tint, so which control belongs to which line is
      // obvious rather than inferred from a shared horizontal position.
      className={`border-b border-ink-200 ${ROW_TINT[row.status]}`}
      // Matched to the row it sits beside. Undefined before the first
      // measurement and whenever the panel is stacked, where the entry simply
      // takes its natural height.
      style={height === undefined ? undefined : { height }}
    >
      {/* Never given a height. This is what the alignment measures, so it has
          to stay free to size itself to what the control actually contains. */}
      <div data-decision-for={row.key} className="px-3 py-5">
      {/* Stacked under the table, an entry has to say which row it decides. */}
      <p className="t-colhead mb-1 text-muted lg:hidden">Line {index + 1}</p>
      <div className="flex gap-1.5">
        {(['accept', 'reject'] as const).map((choice) => (
          <button
            key={choice}
            type="button"
            aria-pressed={current === choice}
            onClick={() => onChange(row.key, current === choice ? 'unset' : choice, remarkText)}
            className={`t-small rounded border px-2.5 py-1 capitalize ${
              current === choice
                ? choice === 'accept'
                  ? 'border-seal bg-seal font-medium text-white'
                  : 'border-flag bg-flag font-medium text-white'
                : 'border-ink-300 bg-surface text-muted hover:text-ink'
            }`}
          >
            {choice}
          </button>
        ))}
      </div>
      {!claimable ? (
        <p className="t-small mt-1 text-muted">Not claimable</p>
      ) : current === 'unset' ? (
        <p className="t-small mt-1 text-muted">Undecided</p>
      ) : null}
      {current === 'reject' ? (
        <input
          value={remarkText}
          onChange={(event) => onChange(row.key, 'reject', event.target.value)}
          placeholder="Why?"
          className="t-small mt-1.5 w-full rounded bg-surface px-2 py-1 text-ink placeholder:text-ink-400"
        />
      ) : null}
      </div>
    </div>
  )
}

function DecisionPanel({
  rows,
  decisions,
  onChange,
  onAll,
  alignment,
}: {
  rows: Array<{
    key: string
    status: SpineState
    billed: unknown
    prescribed: unknown
    findings: unknown[]
  }>
  decisions: Decisions
  onChange: (key: string, decision: 'accept' | 'reject' | 'unset', remark?: string) => void
  onAll: (decision: 'accept' | 'reject') => void
  alignment: Alignment
}) {
  return (
    <div className="w-full border-t border-ink-200 bg-surface lg:w-44 lg:shrink-0 lg:border-t-0 lg:border-l">
      <div
        className="t-colhead flex items-end border-b border-ink-200 px-3 pt-3 pb-2.5 lg:pt-0"
        style={alignment.head === 0 ? undefined : { height: alignment.head }}
      >
        Decision
      </div>
      {rows.map((row, index) => (
        <DecisionEntry
          key={row.key}
          row={row}
          index={index}
          decisions={decisions}
          onChange={onChange}
          height={alignment.rows[row.key]}
        />
      ))}
      <div className="flex flex-col gap-2 px-3 py-3">
        <button
          type="button"
          onClick={() => onAll('accept')}
          className="t-small rounded border border-ink-300 px-3 py-1.5 text-ink hover:bg-paper"
        >
          Accept all
        </button>
        <button
          type="button"
          onClick={() => onAll('reject')}
          className="t-small rounded border border-ink-300 px-3 py-1.5 text-ink hover:bg-paper"
        >
          Reject all
        </button>
      </div>
    </div>
  )
}

function RowNumber({ index }: { index: number }) {
  return (
    <td className="t-small px-4 py-5 align-top text-muted tabular-nums">{index + 1}</td>
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

/**
 * The changed value itself, marked hard.
 *
 * This is where the emphasis belongs. The row tint says which state a line is
 * in; this says which value is the problem, and it has to win against the tint
 * behind it without the tint having to shout.
 */
const MARK_CLASS: Record<Severity, string> = {
  critical: 'bg-flag font-semibold text-white',
  warning: 'bg-caution font-semibold text-white',
  info: 'bg-ink-200 text-ink',
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
      className="t-colhead border-b border-l border-ink-200 px-4 pt-3 pb-1.5 text-center first:border-l-0"
    >
      {label}
    </th>
  )
}

function SubHead({ label, side }: { label: string; side?: 'rx' | 'bill' }) {
  return (
    <th
      scope="col"
      className={`t-colhead px-3 pb-2 text-left ${
        side === 'rx' ? 'border-l border-ink-200' : ''
      }`}
    >
      {label}
    </th>
  )
}

function StatusCell({ status, partial }: { status: SpineState; partial: boolean }) {
  return (
    <td className="px-4 py-5 align-top whitespace-nowrap">
      <span className="inline-flex items-center gap-2">
        <SpineMark state={status} />
        <span className="t-colhead text-ink">{STATUS_LABEL[status]}</span>
        {partial ? (
          <span
            className="t-colhead text-unknown"
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
    <td className="min-w-[14rem] border-l border-ink-200 px-4 py-5 align-top">
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
  decisions,
  onDecision,
  onDecideAll,
}: {
  result: ReconciliationResult
  onHover?: (row: { prescribedId: string | null; billedId: string | null } | null) => void
  technical?: boolean
  filter?: RowFilter
  decisions: Decisions
  onDecision: (key: string, decision: 'accept' | 'reject' | 'unset', remark?: string) => void
  onDecideAll: (decision: 'accept' | 'reject') => void
}) {
  const { ref, alignment } = useRowAlignment()
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
    <div ref={ref} className="flex flex-col lg:flex-row lg:items-start">
      {/* The table scrolls under the panel; the panel never moves. */}
      <div className="w-full overflow-x-auto lg:min-w-0 lg:flex-1">
        <table className="w-full min-w-[54rem] border-collapse">
        <thead>
          <tr>
            <th rowSpan={2} scope="col" className="t-colhead px-4 pb-3 text-left">
              #
            </th>
            <th rowSpan={2} scope="col" className="t-colhead px-4 pb-3 text-left">
              Status
            </th>
            {/* Remark leads: it carries the summary and is what a reviewer
                reads first. */}
            <th
              rowSpan={2}
              scope="col"
              className="t-colhead min-w-[14rem] border-l border-ink-200 px-4 pb-3 text-left"
            >
              Remark
            </th>
            <GroupHead label="Drug" />
            <th
              rowSpan={2}
              scope="col"
              className="t-colhead max-w-[13rem] border-l border-ink-200 px-4 pb-3 text-left"
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
                data-row-key={row.key}
                // A minimum, not a height: CSS lets a `tr` grow past it, so the
                // row renders at whichever of the two is taller. Measured from
                // the control rather than guessed at.
                style={
                  alignment.aligned ? { height: alignment.needs[row.key] } : undefined
                }
                className={`border-b border-ink-200 align-top ${ROW_TINT[row.status]}`}
              >
                <RowNumber index={index} />
                <StatusCell status={row.status} partial={row.partial} />
                <RemarkCell text={remark(row.codes, row.findings)} />
                <td className="border-l border-ink-200 px-4 py-5">
                  <span className={at('drug')}>
                    <Val muted={quiet}>{row.prescribed?.drug_name}</Val>
                  </span>
                </td>
                <td className="px-4 py-5">
                  <span className={at('drug')}>
                    <Val muted={quiet}>{row.billed?.drug_name}</Val>
                  </span>
                </td>
                <td className="max-w-[13rem] border-l border-ink-200 px-4 py-5 break-words">
                  <Val muted>{saltOf(row, canonical)}</Val>
                </td>
                <td className="border-l border-ink-200 px-4 py-5">
                  <span className={at('strength')}>
                    <Val muted={quiet}>{strengthOf(row.prescribed)}</Val>
                  </span>
                </td>
                <td className="px-4 py-5">
                  <span className={at('strength')}>
                    <Val muted={quiet}>{strengthOf(row.billed)}</Val>
                  </span>
                </td>
                <td className="border-l border-ink-200 px-4 py-5">
                  <span className={at('form')}>
                    <Val muted={quiet}>{row.prescribed?.form}</Val>
                  </span>
                </td>
                <td className="px-4 py-5">
                  <span className={at('form')}>
                    <Val muted={quiet}>{row.billed?.form}</Val>
                  </span>
                </td>
                <td className="border-l border-ink-200 px-4 py-5">
                  <span className={at('qty')}>
                    <Val muted={quiet}>{expectedQty(row.findings)}</Val>
                  </span>
                </td>
                <td className="px-4 py-5">
                  <span className={at('qty')}>
                    <Val muted={quiet}>{billedQty(row.billed)}</Val>
                  </span>
                </td>
                {technical ? (
                  <>
                    <td className="border-l border-ink-200 px-4 py-5">
                      <span className="t-data text-muted" title={row.prescribed?.raw_text}>
                        {row.prescribed?.item_id ?? '—'}
                      </span>
                    </td>
                    <td className="px-4 py-5">
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
      <DecisionPanel
        rows={rows}
        decisions={decisions}
        onChange={onDecision}
        onAll={onDecideAll}
        alignment={alignment}
      />
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
  decisions,
  onDecision,
  onDecideAll,
}: {
  result: ReconciliationResult
  onHover?: (row: { prescribedId: string | null; billedId: string | null } | null) => void
  technical?: boolean
  filter?: RowFilter
  decisions: Decisions
  onDecision: (key: string, decision: 'accept' | 'reject' | 'unset', remark?: string) => void
  onDecideAll: (decision: 'accept' | 'reject') => void
}) {
  const { ref, alignment } = useRowAlignment()
  const all = testRowsOf(result)
  const rows = applyFilter(all, filter)
  /**
   * What a lab row says when nothing is wrong with it.
   *
   * A blank cell reads as missing data, so a row that matched says so. The
   * panel wording is per-row now that the engine states which ordered test
   * each billed line was counted against.
   */
  const labRemark = (row: TestRow): string => {
    if (row.findings.length > 0) return testRemark(row.codes, row.findings)
    if (row.prescribed === null && row.billed !== null) {
      return row.coveredBy
        ? `Billed as part of ${row.coveredBy}`
        : 'Billed as part of an ordered panel'
    }
    if (row.prescribed !== null && (row.coversCount ?? 0) > 1) {
      return `Ordered as a panel — billed as ${row.coversCount} itemised lines`
    }
    if (row.prescribed !== null && row.billed !== null) return 'Ordered and billed'
    return testRemark(row.codes, row.findings)
  }

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
    <div ref={ref} className="flex flex-col lg:flex-row lg:items-start">
      <div className="w-full overflow-x-auto lg:min-w-0 lg:flex-1">
        <table className="w-full min-w-[40rem] border-collapse">
        <thead>
          <tr>
            <th rowSpan={2} scope="col" className="t-colhead px-4 pb-3 text-left">
              #
            </th>
            <th rowSpan={2} scope="col" className="t-colhead px-4 pb-3 text-left">
              Status
            </th>
            <th
              rowSpan={2}
              scope="col"
              className="t-colhead min-w-[14rem] border-l border-ink-200 px-4 pb-3 text-left"
            >
              Remark
            </th>
            <GroupHead label="Test" />
            <th rowSpan={2} scope="col" className="t-colhead border-l border-ink-200 px-4 pb-3 text-left">
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
                data-row-key={row.key}
                // A minimum, not a height: CSS lets a `tr` grow past it, so the
                // row renders at whichever of the two is taller. Measured from
                // the control rather than guessed at.
                style={
                  alignment.aligned ? { height: alignment.needs[row.key] } : undefined
                }
                className={`border-b border-ink-200 align-top ${ROW_TINT[row.status]}`}
              >
                <RowNumber index={index} />
                <StatusCell status={row.status} partial={row.partial} />
                <RemarkCell text={labRemark(row)} />
                <td className="border-l border-ink-200 px-4 py-5">
                  {row.coveredBy ? (
                    // Indented and repeated, so four billed analytes read as one
                    // ordered panel rather than as three unmatched lines.
                    <span className="inline-flex items-baseline gap-1.5 pl-4">
                      <span className="t-small text-ink-400" aria-hidden="true">
                        &#8627;
                      </span>
                      <span className="t-data text-muted">{row.coveredBy}</span>
                    </span>
                  ) : (
                    <Val muted={quiet}>{testLabel(row.prescribed)}</Val>
                  )}
                </td>
                <td className="px-4 py-5">
                  <Val muted={quiet}>{testLabel(row.billed)}</Val>
                </td>
                <td className="border-l border-ink-200 px-4 py-5">
                  <Val muted>{panelOf(row)}</Val>
                </td>
                {technical ? (
                  <>
                    <td className="border-l border-ink-200 px-4 py-5">
                      <span className="t-data text-muted" title={row.prescribed?.raw_text}>
                        {row.prescribed?.item_id ?? '—'}
                      </span>
                    </td>
                    <td className="px-4 py-5">
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
      <DecisionPanel
        rows={rows}
        decisions={decisions}
        onChange={onDecision}
        onAll={onDecideAll}
        alignment={alignment}
      />
    </div>
  )
}
