import { describe, expect, it } from 'vitest'
import { medicineRowsOf, testRowsOf } from './rows'
import type { ReconciliationResult } from '../types/api'

/**
 * The row key is a contract with the reports.
 *
 * A decision recorded here is looked up in the PDF, the workbook and the JSON
 * by `rxconcile.export.common.row_key`. If either side changes its naming the
 * reports quietly print "Not decided" beside every accepted line and nothing
 * fails. `api/tests/test_export_decisions.py` asserts the same seven forms.
 */
const RESULT = {
  prescription: {
    items: [{ item_id: 'rx-01' }, { item_id: 'rx-02' }],
    tests: [{ item_id: 't-01' }, { item_id: 't-02' }],
    investigations_present: true,
  },
  bill: {
    items: [{ item_id: 'bill-01' }, { item_id: 'bill-02' }],
    tests: [{ item_id: 'bt-01' }, { item_id: 'bt-02' }, { item_id: 'bt-03' }],
  },
  matched_pairs: [{ prescribed_id: 'rx-01', billed_id: 'bill-01', similarity: 1 }],
  unmatched_prescribed: ['rx-02'],
  unmatched_billed: ['bill-02'],
  matched_tests: [{ prescribed_id: 't-01', billed_id: 'bt-01' }],
  unmatched_prescribed_tests: ['t-02'],
  unmatched_billed_tests: ['bt-02'],
  findings: [],
} as unknown as ReconciliationResult

describe('row keys', () => {
  it('names medicine rows the way the reports look them up', () => {
    expect(medicineRowsOf(RESULT).map((r) => r.key)).toEqual([
      'rx-01-bill-01',
      'rx-only-rx-02',
      'bill-only-bill-02',
    ])
  })

  it('names lab rows the way the reports look them up', () => {
    expect(testRowsOf(RESULT).map((r) => r.key)).toEqual([
      't-01-bt-01',
      // bt-03 is accounted for by neither a pair nor an unmatched list: it was
      // billed under a panel that was ordered, and carries its own form.
      'covered-bt-03',
      'rxt-t-02',
      'bt-bt-02',
    ])
  })
})
