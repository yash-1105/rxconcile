/**
 * Building the comparison rows, once.
 *
 * The tiles count them, the filters narrow them and the tables render them, so
 * a row exists in exactly one place. Splitting this out is what stops a tile
 * saying four when the table below it shows five.
 */

import type {
  BilledItem,
  BilledTest,
  Finding,
  PrescribedItem,
  PrescribedTest,
  ReconciliationResult,
} from '../types/api'
import type { SpineState } from '../components/Spine'
import { statusFrom } from './rowStatus'

/** What the table filter is showing. */
export type RowFilter = 'all' | 'matched' | 'problems' | 'out-of-scope'

export const ROW_FILTERS: ReadonlyArray<{ value: RowFilter; label: string }> = [
  { value: 'all', label: 'All' },
  { value: 'matched', label: 'Matched only' },
  { value: 'problems', label: 'Problems only' },
  { value: 'out-of-scope', label: 'Out of scope' },
]

interface MedRow {
  key: string
  prescribed: PrescribedItem | null
  billed: BilledItem | null
  similarity: number | null
  findings: Finding[]
  codes: string[]
  status: SpineState
  /** A check on this row could not be concluded. A marker, never a downgrade. */
  partial: boolean
}

function medicineRows(result: ReconciliationResult): MedRow[] {
  const rx = new Map(result.prescription.items.map((i) => [i.item_id, i]))
  const bill = new Map(result.bill.items.map((i) => [i.item_id, i]))
  const rows: MedRow[] = []

  const build = (
    key: string,
    prescribed: PrescribedItem | null,
    billed: BilledItem | null,
    similarity: number | null,
    findings: Finding[],
  ): MedRow => {
    const paired = prescribed !== null && billed !== null
    const { state, partial } = statusFrom(findings, { paired })
    return {
      key,
      prescribed,
      billed,
      similarity,
      findings,
      codes: findings.map((f) => f.rule_code),
      status: state,
      partial,
    }
  }

  for (const pair of result.matched_pairs) {
    const findings = result.findings.filter(
      (f) => f.prescribed_ref === pair.prescribed_id && f.billed_ref === pair.billed_id,
    )
    rows.push(
      build(
        `${pair.prescribed_id}-${pair.billed_id}`,
        rx.get(pair.prescribed_id) ?? null,
        bill.get(pair.billed_id) ?? null,
        pair.similarity,
        findings,
      ),
    )
  }
  for (const id of result.unmatched_prescribed) {
    const findings = result.findings.filter((f) => f.prescribed_ref === id)
    rows.push(build(`rx-only-${id}`, rx.get(id) ?? null, null, null, findings))
  }
  for (const id of result.unmatched_billed) {
    const findings = result.findings.filter((f) => f.billed_ref === id)
    rows.push(build(`bill-only-${id}`, null, bill.get(id) ?? null, null, findings))
  }
  return rows
}

/**
 * The salt behind a row, from the canonical match the ENGINE resolved.
 *
 * This used to read the salt out of finding details, so it only appeared when a
 * BRAND_SUBSTITUTION or SCHEDULE_H_UNBACKED happened to fire — Augmentin, Pan-D
 * and Montair-LC resolve perfectly in the dictionary and still showed nothing.
 * The response now reports the match for every line.
 *
 * The transcribed `salt` is the fallback, not the source: it is what the model
 * read off the page, which is usually null. An unresolved drug stays an
 * em-dash; a salt is never inferred from a brand the dictionary does not know.
 */
interface TestRow {
  key: string
  prescribed: PrescribedTest | null
  billed: BilledTest | null
  findings: Finding[]
  codes: string[]
  status: SpineState
  partial: boolean
}

function testRows(result: ReconciliationResult): TestRow[] {
  // Stored history records predating lab reconciliation are returned as the raw
  // blob they were saved as, with no `tests` arrays at all. Defaulting here
  // keeps an old record readable instead of blanking the screen.
  const rx = new Map((result.prescription.tests ?? []).map((t) => [t.item_id, t]))
  const bill = new Map((result.bill.tests ?? []).map((t) => [t.item_id, t]))
  const rows: TestRow[] = []
  const build = (
    key: string,
    prescribed: PrescribedTest | null,
    billed: BilledTest | null,
    findings: Finding[],
  ): TestRow => {
    const paired = prescribed !== null && billed !== null
    const { state, partial } = statusFrom(findings, { paired })
    return {
      key,
      prescribed,
      billed,
      findings,
      codes: findings.map((f) => f.rule_code),
      status: state,
      partial,
    }
  }

  for (const pair of result.matched_tests ?? []) {
    const findings = result.findings.filter(
      (f) => f.prescribed_ref === pair.prescribed_id && f.billed_ref === pair.billed_id,
    )
    rows.push(
      build(
        `${pair.prescribed_id}-${pair.billed_id}`,
        rx.get(pair.prescribed_id) ?? null,
        bill.get(pair.billed_id) ?? null,
        findings,
      ),
    )
  }
  // A panel match consumes every billed line that covered it, but the response
  // names only the primary one in `matched_tests`. Without this, an ordered CBC
  // billed as six analytes would show one line and silently drop five -- a table
  // that does not account for every line on the bill is worse than no table.
  //
  // They are NOT attributed to a particular ordered panel here: with two matched
  // panels the response does not say which line covered which, and guessing
  // would be an invention. They are shown as what is certain -- billed, and
  // covered by something that was ordered.
  const accounted = new Set([
    ...(result.matched_tests ?? []).map((p) => p.billed_id),
    ...(result.unmatched_billed_tests ?? []),
  ])
  for (const test of result.bill.tests ?? []) {
    if (accounted.has(test.item_id)) continue
    rows.push({
      key: `covered-${test.item_id}`,
      prescribed: null,
      billed: test,
      findings: [],
      codes: [],
      // Covered by a panel that was ordered: a positive result, not an absence.
      status: 'clean',
      partial: false,
    })
  }
  for (const id of result.unmatched_prescribed_tests ?? []) {
    rows.push(
      build(`rxt-${id}`, rx.get(id) ?? null, null, result.findings.filter((f) => f.prescribed_ref === id)),
    )
  }
  for (const id of result.unmatched_billed_tests ?? []) {
    rows.push(
      build(`bt-${id}`, null, bill.get(id) ?? null, result.findings.filter((f) => f.billed_ref === id)),
    )
  }

  return rows
}

/**
 * The panel a row belongs to.
 *
 * Prefers the canonical panel the decomposition resolved, which is what makes
 * one ordered "LFT" and six billed analytes legible as a single thing.
 */

/**
 * A legal brand substitution gets its own state.
 *
 * It is a match — same salt, different brand — but a reviewer wants to see at a
 * glance that the brand changed, so it is neither plain green nor a problem.
 */
function withSubstitution<T extends { findings: Finding[]; status: SpineState }>(row: T): T {
  if (row.status !== 'clean') return row
  const substituted = row.findings.some((f) => f.rule_code === 'BRAND_SUBSTITUTION')
  return substituted ? { ...row, status: 'substitution' as SpineState } : row
}

export function medicineRowsOf(result: ReconciliationResult): MedRow[] {
  return medicineRows(result).map(withSubstitution)
}

export function testRowsOf(result: ReconciliationResult): TestRow[] {
  return testRows(result).map(withSubstitution)
}

export type { MedRow, TestRow }

/** Rows a filter keeps. */
export function applyFilter<T extends { status: SpineState }>(
  rows: T[],
  filter: RowFilter,
): T[] {
  if (filter === 'all') return rows
  if (filter === 'matched') {
    return rows.filter((row) => row.status === 'clean' || row.status === 'substitution')
  }
  if (filter === 'problems') {
    return rows.filter((row) => row.status === 'problem' || row.status === 'warning')
  }
  return rows.filter((row) => row.status === 'out-of-scope' || row.status === 'unchecked')
}

export interface RowCounts {
  matched: number
  problems: number
}

export function countRows<T extends { status: SpineState }>(rows: T[]): RowCounts {
  return {
    matched: rows.filter((r) => r.status === 'clean' || r.status === 'substitution').length,
    problems: rows.filter((r) => r.status === 'problem' || r.status === 'warning').length,
  }
}
