import { describe, expect, it } from 'vitest'
import { medicineRowsOf, testRowsOf } from './rows'
import type { ReconciliationResult } from '../types/api'

/**
 * Which findings reach a matched row.
 *
 * A finding about a matched pair may name both halves or only one. EXPIRED_ITEM
 * names the BILLED line alone — expiry is a property of what was dispensed —
 * and the old filter required both refs to match, so it never reached its row.
 * A critical "dispensed after it expired" rendered as a clean brand
 * substitution, which is the one failure mode the row-status precedence exists
 * to prevent.
 */
function finding(
  rule_code: string,
  severity: string,
  refs: { rx?: string | null; bill?: string | null } = {},
) {
  return {
    rule_code,
    severity,
    message: rule_code,
    prescribed_ref: refs.rx ?? null,
    billed_ref: refs.bill ?? null,
    detail: {},
  }
}

const RESULT = {
  prescription: { items: [{ item_id: 'rx-01' }, { item_id: 'rx-02' }], tests: [] },
  bill: { items: [{ item_id: 'bill-01' }, { item_id: 'bill-02' }], tests: [] },
  matched_pairs: [
    { prescribed_id: 'rx-01', billed_id: 'bill-01', similarity: 1 },
    { prescribed_id: 'rx-02', billed_id: 'bill-02', similarity: 1 },
  ],
  unmatched_prescribed: [],
  unmatched_billed: [],
  matched_tests: [],
  unmatched_prescribed_tests: [],
  unmatched_billed_tests: [],
  findings: [
    finding('BRAND_SUBSTITUTION', 'info', { rx: 'rx-01', bill: 'bill-01' }),
    // Billed side only. This is the one that used to vanish.
    finding('EXPIRED_ITEM', 'critical', { bill: 'bill-01' }),
    // Prescribed side only, on the OTHER pair.
    finding('LOW_CONFIDENCE_FIELD', 'info', { rx: 'rx-02' }),
    // Document-level: belongs to no row at all.
    finding('DUPLICATE_BILL', 'critical'),
  ],
} as unknown as ReconciliationResult

describe('findings on a matched row', () => {
  it('includes a finding that names only the billed half', () => {
    const row = medicineRowsOf(RESULT)[0]!
    expect(row.codes).toContain('EXPIRED_ITEM')
  })

  it('makes that row a problem, because the finding is critical', () => {
    // Without EXPIRED_ITEM this row is an info-only brand substitution.
    expect(medicineRowsOf(RESULT)[0]!.status).toBe('problem')
  })

  it('includes a finding that names only the prescribed half', () => {
    expect(medicineRowsOf(RESULT)[1]!.codes).toContain('LOW_CONFIDENCE_FIELD')
  })

  it('never puts a one-sided finding on the wrong row', () => {
    expect(medicineRowsOf(RESULT)[1]!.codes).not.toContain('EXPIRED_ITEM')
    expect(medicineRowsOf(RESULT)[0]!.codes).not.toContain('LOW_CONFIDENCE_FIELD')
  })

  it('leaves document-level findings off every row', () => {
    // They concern no line, and a row is not the place to answer for them.
    for (const row of medicineRowsOf(RESULT)) {
      expect(row.codes).not.toContain('DUPLICATE_BILL')
    }
  })

  it('applies the same rule to lab tests', () => {
    const result = {
      ...RESULT,
      prescription: { items: [], tests: [{ item_id: 't-01' }] },
      bill: { items: [], tests: [{ item_id: 'bt-01' }] },
      matched_pairs: [],
      matched_tests: [{ prescribed_id: 't-01', billed_id: 'bt-01' }],
      findings: [finding('TEST_DUPLICATE', 'critical', { bill: 'bt-01' })],
    } as unknown as ReconciliationResult
    const row = testRowsOf(result)[0]!
    expect(row.codes).toContain('TEST_DUPLICATE')
    expect(row.status).toBe('problem')
  })
})
