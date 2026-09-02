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

/**
 * The findings that belong to a matched row.
 *
 * A finding about a matched pair may name BOTH halves, or only one. EXPIRED_ITEM
 * is a property of the billed line alone and carries no prescribed ref; requiring
 * both refs to match dropped it from its row entirely, so a critical "dispensed
 * after it expired" rendered as a clean brand substitution.
 *
 * A finding belongs to this row when every ref it does carry points at this row,
 * and it carries at least one. Both refs null is a document-level finding, which
 * belongs to no row.
 */
function findingsForPair(
  findings: Finding[],
  prescribedId: string,
  billedId: string,
): Finding[] {
  return findings.filter((f) => {
    if (f.prescribed_ref === null && f.billed_ref === null) return false
    if (f.prescribed_ref !== null && f.prescribed_ref !== prescribedId) return false
    if (f.billed_ref !== null && f.billed_ref !== billedId) return false
    return true
  })
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
    const findings = findingsForPair(result.findings, pair.prescribed_id, pair.billed_id)
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
  /**
   * The ordered test this billed line was counted against, when it is one of
   * several lines covering a single ordered panel.
   *
   * Read from `MatchedPair.covers`, which the ENGINE states. It was previously
   * left blank because the response named only the primary line, and guessing
   * which panel the rest belonged to would have been an invention.
   */
  coveredBy?: string | null
  /** How many billed lines cover this ordered test, when it is a panel. */
  coversCount?: number
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
    const findings = findingsForPair(result.findings, pair.prescribed_id, pair.billed_id)
    rows.push({
      ...build(
        `${pair.prescribed_id}-${pair.billed_id}`,
        rx.get(pair.prescribed_id) ?? null,
        bill.get(pair.billed_id) ?? null,
        findings,
      ),
      coversCount: (pair.covers ?? []).length,
    })
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
  // Which ordered test each covering line belongs to, as the engine reported it.
  const coveringPanel = new Map<string, string>()
  for (const pair of result.matched_tests ?? []) {
    const orderedName = testLabel(rx.get(pair.prescribed_id))
    for (const billedId of pair.covers ?? []) {
      if (orderedName) coveringPanel.set(billedId, orderedName)
    }
  }
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
      coveredBy: coveringPanel.get(test.item_id) ?? null,
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

/**
 * What to show for a test line.
 *
 * `test_name` is null whenever the extractor read the line but could not
 * isolate a name from it — "Plasma Glucose < F / PP after a month" came back
 * exactly that way. Rendering only `test_name` then produced a row that was an
 * em-dash in every column: a row about nothing, on a page that had been read
 * perfectly.
 *
 * `raw_text` is never nulled. It is the line as it appears on the document, so
 * it is always the honest fallback.
 */
export function testLabel(
  test: { test_name?: string | null; raw_text?: string | null } | null | undefined,
): string | null {
  if (!test) return null
  const name = test.test_name?.trim()
  if (name) return name
  const raw = test.raw_text?.trim()
  return raw || null
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

// ---------------------------------------------------------------------------
// What can be claimed, and what the reviewer decided about it
// ---------------------------------------------------------------------------

/** Accept, reject, or not yet decided. */
export type Decision = 'accept' | 'reject' | 'unset'

export interface LineDecision {
  decision: Decision
  remark?: string
}

/** Every decision on a scan, keyed by row key. */
export type Decisions = Record<string, LineDecision>

/**
 * Whether a line can be claimed at all.
 *
 * Claimable means matched: a medicine paired to a prescription line, or a lab
 * test covered by an ordered test. A billed line with nothing behind it and a
 * line that is not a medicine are never claimable, whatever anyone decides
 * about them — deciding is how a reviewer records a judgement, not how they
 * make something reimbursable.
 *
 * A line with no printed amount is not claimable either. There is no figure to
 * add, and assuming one would put an invented number into a total.
 */
export function isClaimable(row: MedRow | TestRow): boolean {
  if (row.status === 'out-of-scope') return false
  if (row.billed === null) return false
  if (row.billed.line_total === null) return false
  // Paired to a prescribed line, or covered by an ordered panel.
  const paired = row.prescribed !== null
  const panelCovered = row.status === 'clean' && row.findings.length === 0
  return paired || panelCovered
}

/** The amount a line would contribute, or zero when it cannot contribute. */
export function claimableAmount(row: MedRow | TestRow): number {
  if (!isClaimable(row)) return 0
  return Number(row.billed?.line_total ?? 0) || 0
}

/**
 * The decision a row starts with.
 *
 * Matched lines default to accept, because that is the answer for the common
 * case. Anything carrying a problem starts UNSET: the reviewer has to look at
 * it, and pre-accepting it would put their name against a judgement they never
 * made.
 */
export function defaultDecision(row: MedRow | TestRow): Decision {
  if (!isClaimable(row)) return 'unset'
  return row.status === 'clean' || row.status === 'substitution' ? 'accept' : 'unset'
}

export function decisionsFor(rows: Array<MedRow | TestRow>, stored?: Decisions): Decisions {
  const out: Decisions = {}
  for (const row of rows) {
    // What was decided last time wins over the default. Without this, reopening
    // a reviewed scan would show every line back at its starting position and
    // then save that over the record -- losing the review by displaying it.
    const kept = stored?.[row.key]
    out[row.key] = kept ?? { decision: defaultDecision(row) }
  }
  return out
}

/**
 * The claim total: accepted, claimable lines and nothing else.
 *
 * Derived from the same rows the tables render, so the figure on screen and the
 * figure sent to the server cannot disagree — and that is the one number a
 * client will check by hand.
 */
export function claimTotal(rows: Array<MedRow | TestRow>, decisions: Decisions): number {
  return rows.reduce((total, row) => {
    const decision = decisions[row.key]?.decision ?? defaultDecision(row)
    return decision === 'accept' ? total + claimableAmount(row) : total
  }, 0)
}

export function countDecisions(
  rows: Array<MedRow | TestRow>,
  decisions: Decisions,
): { accepted: number; rejected: number; undecided: number } {
  let accepted = 0
  let rejected = 0
  let undecided = 0
  for (const row of rows) {
    if (!isClaimable(row)) continue
    const decision = decisions[row.key]?.decision ?? defaultDecision(row)
    if (decision === 'accept') accepted += 1
    else if (decision === 'reject') rejected += 1
    else undecided += 1
  }
  return { accepted, rejected, undecided }
}
