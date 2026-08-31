/**
 * The two comparison tables: medicines and lab tests.
 *
 * Each attribute is ONE column showing "prescribed → billed", not two columns
 * under a grouped heading. Six columns became three, the table fits a 1280px
 * viewport without scrolling sideways, and Decision — the only thing a reviewer
 * is here to do — sits fourth instead of past the right edge.
 *
 * Presentation only. Every value shown is read from the response as computed —
 * nothing here derives a quantity, resolves a drug or decides a status. Where a
 * value is absent it renders as an em-dash, never as a zero and never as a
 * guess.
 */

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
 * Row colour, alongside the mark and the status word.
 *
 * Saturated enough to scan without reading. The mark and the word carry the
 * same meaning in shape and in text, so the table survives being printed or
 * read by someone who cannot separate the hues.
 */
const ROW_TINT: Record<SpineState, string> = {
  clean: 'bg-tint-clean',
  substitution: 'bg-tint-substitution',
  warning: 'bg-tint-warning',
  problem: 'bg-tint-problem',
  unchecked: 'bg-tint-neutral',
  'out-of-scope': 'bg-tint-neutral',
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
 * Accept or reject one line, with a remark when it is rejected.
 *
 * Shown on every row so a reviewer can annotate anything, but only a CLAIMABLE
 * line moves the total — accepting a delivery charge does not make it
 * reimbursable, and the label says so rather than letting the control imply it.
 */
function DecisionCell({
  row,
  decisions,
  onChange,
}: {
  row: { key: string; status: SpineState; billed: unknown; prescribed: unknown; findings: unknown[] }
  decisions: Decisions
  onChange: (key: string, decision: 'accept' | 'reject' | 'unset', remark?: string) => void
}) {
  const claimable = isClaimable(row as never)
  const current = decisions[row.key]?.decision ?? defaultDecision(row as never)
  const remarkText = decisions[row.key]?.remark ?? ''
  return (
    <td className="border-l border-ink-200/60 px-3 py-5 align-top">
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
                : 'border-ink-400/60 bg-surface/80 text-muted hover:text-ink'
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
          className="t-small mt-1.5 w-full rounded bg-surface/90 px-2 py-1 text-ink placeholder:text-ink-400"
        />
      ) : null}
    </td>
  )
}

export function BulkDecisions({
  onAll,
}: {
  onAll: (decision: 'accept' | 'reject') => void
}) {
  return (
    <div className="flex gap-2 border-t border-ink-200 px-4 py-3">
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
  )
}

function RowNumber({ index }: { index: number }) {
  return (
    <td className="t-small px-3 py-5 align-top text-muted tabular-nums">{index + 1}</td>
  )
}

/** An absent value. Never a zero, never a blank cell that reads as agreement. */
function Absent() {
  return (
    <span className="t-data text-unknown" title="Not present on the document">
      —
    </span>
  )
}

/**
 * One attribute, as prescribed and as billed.
 *
 * When only one side exists there is NO arrow: an unmatched line was never
 * compared, and "Hexigel → —" would draw a comparison that did not happen. The
 * row status and the remark already say what became of it.
 *
 * When both sides agree the value is printed once. Repeating "tablet → tablet"
 * on every clean row is noise that buries the rows where the two differ.
 */
function Pair({
  rx,
  bill,
  marking,
  quiet,
  mono = true,
}: {
  rx: string | null
  bill: string | null
  /** How loudly this field is flagged, from the engine's severity. */
  marking?: string
  quiet?: boolean
  mono?: boolean
}) {
  const type = mono ? 't-data' : 't-small'
  const tone = quiet ? 'text-muted' : 'text-ink'
  if (rx === null && bill === null) return <Absent />
  if (rx === null || bill === null) {
    return <span className={`${type} ${tone} whitespace-nowrap`}>{rx ?? bill}</span>
  }
  if (rx === bill) return <span className={`${type} ${tone} whitespace-nowrap`}>{rx}</span>
  return (
    <span className="inline-flex flex-wrap items-baseline gap-1">
      <span className={`${type} whitespace-nowrap text-muted`}>{rx}</span>
      <span className="t-small text-ink-400" aria-label="billed as">
        →
      </span>
      {/* The billed side is the one that changed, so it is the one marked. */}
      <span className={`${type} whitespace-nowrap ${marking ?? tone}`}>{bill}</span>
    </span>
  )
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

/** A changed value has to be unmissable against an already-tinted row. */
const MARK_CLASS: Record<Severity, string> = {
  critical: 'rounded bg-flag px-1.5 font-semibold text-white',
  warning: 'rounded bg-caution px-1.5 font-semibold text-white',
  info: 'rounded bg-ink-200 px-1.5 text-ink',
}

/** The loudest marking any finding puts on one field of a row. */
function marksFor(
  findings: Finding[],
): Partial<Record<'drug' | 'strength' | 'form' | 'qty', Severity>> {
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

/**
 * Column widths, as shares of the table.
 *
 * Fixed rather than automatic. Left to the browser, the longest salt in the
 * batch decided how much room the Drug column took and the Remark sentence got
 * whatever was left — so the column carrying the explanation was the narrowest
 * on the page. `max-width` does not bind on a cell in the automatic algorithm,
 * so the layout has to be stated.
 */
const MEDICINE_COLS: readonly string[] = [
  '3.5%', // #
  '14%', //  Status — sized for the longest word, SUBSTITUTED
  '23%', //  Remark — the sentence, and the widest column on purpose
  '14.5%', // Decision
  '17%', //  Drug, with the salt wrapping beneath it
  '10%', //  Strength
  '7.5%', // Form
  '11.5%', // Qty
]

const TEST_COLS: readonly string[] = ['4%', '13%', '30%', '16%', '37%']

/** Technical mode adds an Ids column, which takes its share off the widest. */
function withIds(widths: readonly string[], technical: boolean): readonly string[] {
  return technical ? [...widths, '10%'] : widths
}

function Columns({ widths }: { widths: readonly string[] }) {
  return (
    <colgroup>
      {widths.map((width, index) => (
        <col key={index} style={{ width }} />
      ))}
    </colgroup>
  )
}

function Head({ label, className = '' }: { label: string; className?: string }) {
  return (
    <th scope="col" className={`t-colhead px-3 pb-2.5 text-left ${className}`}>
      {label}
    </th>
  )
}

function StatusCell({ status, partial }: { status: SpineState; partial: boolean }) {
  return (
    <td className="px-3 py-5 align-top">
      <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
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
 * Remark sits third, and wraps.
 *
 * It carries the summary of the row, so it is read before any detail. No fixed
 * width: forcing one was half of what pushed the table past the viewport.
 */
function RemarkCell({ text }: { text: string }) {
  return (
    <td className="border-l border-ink-200/60 px-3 py-5 align-top">
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
}: {
  result: ReconciliationResult
  onHover?: (row: { prescribedId: string | null; billedId: string | null } | null) => void
  technical?: boolean
  filter?: RowFilter
  decisions: Decisions
  onDecision: (key: string, decision: 'accept' | 'reject' | 'unset', remark?: string) => void
}) {
  const rows = applyFilter(medicineRowsOf(result), filter)
  const canonical = new Map((result.canonical ?? []).map((c) => [c.item_id, c]))
  if (rows.length === 0) {
    return (
      <p className="t-small px-4 py-4 text-muted">
        {filter === 'all'
          ? 'Neither document carries a medicine line. Nothing to compare here.'
          : 'No lines match this filter.'}
      </p>
    )
  }
  return (
    <table className="w-full min-w-[46rem] table-fixed border-collapse">
      <Columns widths={withIds(MEDICINE_COLS, technical)} />
      <thead>
        <tr className="border-b border-ink-300">
          <Head label="#" />
          <Head label="Status" />
          <Head label="Remark" className="border-l border-ink-200/60" />
          <Head label="Decision" className="border-l border-ink-200/60" />
          <Head label="Drug" className="border-l border-ink-200/60" />
          <Head label="Strength" />
          <Head label="Form" />
          <Head label="Qty" />
          {technical ? <Head label="Ids" className="border-l border-ink-200/60" /> : null}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => {
          const quiet = row.status === 'clean'
          const m = marksFor(row.findings)
          // Only mark a pair when both halves exist: on an unmatched line the
          // row status already says everything, and painting a lone cell would
          // imply a comparison that never happened.
          const pair = row.prescribed !== null && row.billed !== null
          const at = (field: 'drug' | 'strength' | 'form' | 'qty') => {
            const severity = pair ? m[field] : undefined
            return severity ? MARK_CLASS[severity] : undefined
          }
          const salt = saltOf(row, canonical)
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
              <DecisionCell row={row} decisions={decisions} onChange={onDecision} />
              <td className="border-l border-ink-200/60 px-3 py-5">
                <Pair
                  rx={row.prescribed?.drug_name ?? null}
                  bill={row.billed?.drug_name ?? null}
                  marking={at('drug')}
                  quiet={quiet}
                />
                {/* Context under the name rather than a column of its own: a
                    salt is worth reading once you care about a row, and not
                    worth a share of the width on every row. */}
                {salt ? (
                  <p className="t-small mt-0.5 break-words text-muted">{salt}</p>
                ) : null}
              </td>
              <td className="px-3 py-5">
                <Pair
                  rx={strengthOf(row.prescribed)}
                  bill={strengthOf(row.billed)}
                  marking={at('strength')}
                  quiet={quiet}
                />
              </td>
              <td className="px-3 py-5">
                <Pair
                  rx={row.prescribed?.form ?? null}
                  bill={row.billed?.form ?? null}
                  marking={at('form')}
                  quiet={quiet}
                  mono={false}
                />
              </td>
              <td className="px-3 py-5">
                <Pair
                  rx={expectedQty(row.findings)}
                  bill={billedQty(row.billed)}
                  marking={at('qty')}
                  quiet={quiet}
                />
              </td>
              {technical ? (
                <td className="border-l border-ink-200/60 px-3 py-5">
                  <span
                    className="t-small text-muted"
                    title={`${row.prescribed?.raw_text ?? ''} / ${row.billed?.raw_text ?? ''}`}
                  >
                    {row.prescribed?.item_id ?? '—'} → {row.billed?.item_id ?? '—'}
                    {row.similarity !== null ? ` · ${row.similarity.toFixed(2)}` : ''}
                  </span>
                </td>
              ) : null}
            </tr>
          )
        })}
      </tbody>
    </table>
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
}: {
  result: ReconciliationResult
  onHover?: (row: { prescribedId: string | null; billedId: string | null } | null) => void
  technical?: boolean
  filter?: RowFilter
  decisions: Decisions
  onDecision: (key: string, decision: 'accept' | 'reject' | 'unset', remark?: string) => void
}) {
  const all = testRowsOf(result)
  const rows = applyFilter(all, filter)
  const coveredCount =
    (result.matched_tests ?? []).length === 1
      ? all.filter((r) => r.prescribed === null && r.billed !== null && r.findings.length === 0)
          .length
      : 0

  if (all.length > 0 && rows.length === 0) {
    return <p className="t-small px-4 py-4 text-muted">No lines match this filter.</p>
  }

  // An empty table reads as a rendering failure or a missed section. These two
  // states are entirely different results and must never render alike.
  if (all.length === 0) {
    // `undefined` on a legacy record is not the same as a measured `null`, and
    // neither may render as "no tests ordered".
    const present = result.prescription.investigations_present ?? null
    if (result.prescription.tests === undefined) {
      return (
        <p className="t-body px-4 py-4 text-muted">
          This result was recorded before lab tests were reconciled, so it carries no
          investigations data. Nothing here says tests were or were not ordered.
        </p>
      )
    }
    if (present === true) {
      return (
        <p className="t-body px-4 py-4 text-ink">
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
        <p className="t-body px-4 py-4 text-muted">
          No investigations ordered on this prescription. Nothing to compare, and nothing
          missing.
        </p>
      )
    }
    return (
      <p className="t-body px-4 py-4 text-ink">
        No investigations section was found on this prescription, but its presence could not be
        confirmed. Read the page before treating this as "no tests ordered".
      </p>
    )
  }

  return (
    <table className="w-full min-w-[34rem] table-fixed border-collapse">
      <Columns widths={withIds(TEST_COLS, technical)} />
      <thead>
        <tr className="border-b border-ink-300">
          <Head label="#" />
          <Head label="Status" />
          <Head label="Remark" className="border-l border-ink-200/60" />
          <Head label="Decision" className="border-l border-ink-200/60" />
          <Head label="Test" className="border-l border-ink-200/60" />
          {technical ? <Head label="Ids" className="border-l border-ink-200/60" /> : null}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => {
          const quiet = row.status === 'clean'
          const panel = panelOf(row)
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
              <DecisionCell row={row} decisions={decisions} onChange={onDecision} />
              <td className="border-l border-ink-200/60 px-3 py-5">
                <Pair
                  rx={row.prescribed?.test_name ?? null}
                  bill={row.billed?.test_name ?? null}
                  quiet={quiet}
                  mono={false}
                />
                {panel ? <p className="t-small mt-0.5 text-muted">Panel: {panel}</p> : null}
              </td>
              {technical ? (
                <td className="border-l border-ink-200/60 px-3 py-5">
                  <span
                    className="t-small text-muted"
                    title={`${row.prescribed?.raw_text ?? ''} / ${row.billed?.raw_text ?? ''}`}
                  >
                    {row.prescribed?.item_id ?? '—'} → {row.billed?.item_id ?? '—'}
                  </span>
                </td>
              ) : null}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
