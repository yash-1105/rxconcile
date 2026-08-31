/**
 * Row status precedence for the comparison tables.
 *
 * Its own module so the rule can be tested without rendering anything, and so
 * there is exactly one definition of it.
 *
 * The precedence is explicit and ordered:
 *
 *     critical > warning > matched > partially verified > nothing checked
 *
 * The defect this replaces was that the WEAKEST check on a row decided its
 * status. A clean brand substitution carrying one unverifiable quantity read as
 * NOT CHECKED — the opposite of what happened. A row that matched on every
 * check that ran is matched; a check that could not run is a quiet marker
 * beside it, never a downgrade of the whole row.
 *
 * NOT CHECKED is reserved for a row where nothing produced a result at all.
 *
 * Severity always comes from the engine. Nothing here re-decides how serious a
 * finding is; it only decides which of the engine's verdicts leads.
 */

import type { Finding } from '../types/api'
import type { SpineState } from '../components/Spine'

/** Findings that say a check was attempted or skipped, never that something is wrong. */
export const UNCHECKED_CODES = new Set([
  'QUANTITY_AMBIGUOUS',
  'STRENGTH_UNIT_UNSTATED',
  'TEST_UNRESOLVED',
  'CHECK_UNAVAILABLE',
])

export interface RowStatus {
  state: SpineState
  /** A check on this row could not be concluded. A marker, never a downgrade. */
  partial: boolean
}

export function statusFrom(findings: Finding[], { paired }: { paired: boolean }): RowStatus {
  const partial = findings.some((f) => UNCHECKED_CODES.has(f.rule_code))
  if (findings.some((f) => f.severity === 'critical')) return { state: 'problem', partial }
  if (findings.some((f) => f.severity === 'warning')) return { state: 'warning', partial }
  // A confirmed non-medicine is its own answer. It was read, it was understood,
  // and it is simply not a medicine — which is neither a problem nor a line
  // nobody managed to check.
  if (findings.some((f) => f.rule_code === 'NON_MEDICINE_ITEM')) {
    return { state: 'out-of-scope', partial: false }
  }
  // The pairing is itself a result: these two lines were matched to each other.
  if (paired) return { state: 'clean', partial }
  return { state: 'unchecked', partial: false }
}
