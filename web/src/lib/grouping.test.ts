import { describe, expect, it } from 'vitest'
import {
  criticalCount,
  discrepancyCount,
  groupByItem,
  hideNonMedicineNoise,
} from './grouping'
import type { Finding, MatchedPair, ReconciliationResult, Severity } from '../types/api'

function finding(
  rule_code: string,
  severity: Severity,
  refs: { rx?: string | null; bill?: string | null } = {},
): Finding {
  return {
    rule_code,
    severity,
    message: rule_code,
    prescribed_ref: refs.rx ?? null,
    billed_ref: refs.bill ?? null,
    detail: {},
  }
}

function result(pairs: MatchedPair[] = []): ReconciliationResult {
  return { matched_pairs: pairs, matched_tests: [] } as unknown as ReconciliationResult
}

describe('grouping findings by the item they concern', () => {
  it('THE ALPRAX CASE: two findings on one billed line become one row', () => {
    const groups = groupByItem(
      [
        finding('BILL_NOT_PRESCRIBED', 'critical', { bill: 'bill-05' }),
        finding('SCHEDULE_H_UNBACKED', 'critical', { bill: 'bill-05' }),
      ],
      result(),
    )
    expect(groups).toHaveLength(1)
    expect(groups[0]!.findings).toHaveLength(2)
  })

  it('SCHEDULE_H_UNBACKED heads its row and is never the hidden "+1 more"', () => {
    const groups = groupByItem(
      [
        finding('BILL_NOT_PRESCRIBED', 'critical', { bill: 'bill-05' }),
        finding('SCHEDULE_H_UNBACKED', 'critical', { bill: 'bill-05' }),
      ],
      result(),
    )
    expect(groups[0]!.headline.rule_code).toBe('SCHEDULE_H_UNBACKED')
  })

  it('pins Schedule H even when a more severe-looking finding sorts first', () => {
    // Same severity, and SALT_DIFFERENT_CLASS would otherwise win on specificity.
    const groups = groupByItem(
      [
        finding('SALT_DIFFERENT_CLASS', 'critical', { bill: 'bill-01' }),
        finding('SCHEDULE_H_UNBACKED', 'critical', { bill: 'bill-01' }),
      ],
      result(),
    )
    expect(groups[0]!.headline.rule_code).toBe('SCHEDULE_H_UNBACKED')
  })

  it('a matched pair is one item, whichever ref a finding carries', () => {
    const groups = groupByItem(
      [
        finding('STRENGTH_MISMATCH', 'critical', { rx: 'rx-01', bill: 'bill-01' }),
        finding('QUANTITY_AMBIGUOUS', 'info', { bill: 'bill-01' }),
        finding('BRAND_SUBSTITUTION', 'info', { rx: 'rx-01' }),
      ],
      result([{ prescribed_id: 'rx-01', billed_id: 'bill-01', similarity: 0.9 }]),
    )
    expect(groups).toHaveLength(1)
    expect(groups[0]!.findings).toHaveLength(3)
    expect(groups[0]!.headline.rule_code).toBe('STRENGTH_MISMATCH')
  })

  it('document-level findings each stay their own row', () => {
    const groups = groupByItem(
      [
        finding('PATIENT_NAME_MISMATCH', 'warning'),
        finding('DATE_ANOMALY', 'warning'),
        finding('ITEM_COUNT_UNSTABLE', 'critical'),
      ],
      result(),
    )
    expect(groups).toHaveLength(3)
    expect(groups.every((g) => g.findings.length === 1)).toBe(true)
  })

  it('the most severe finding sets the row severity', () => {
    const groups = groupByItem(
      [
        finding('QUANTITY_AMBIGUOUS', 'info', { bill: 'bill-01' }),
        finding('FORM_MISMATCH', 'warning', { bill: 'bill-01' }),
      ],
      result(),
    )
    expect(groups[0]!.severity).toBe('warning')
    expect(groups[0]!.headline.rule_code).toBe('FORM_MISMATCH')
  })

  it('a tie on severity is broken by the more specific message', () => {
    const groups = groupByItem(
      [
        finding('RX_NOT_BILLED', 'critical', { rx: 'rx-02' }),
        finding('SALT_DIFFERENT_CLASS', 'critical', { rx: 'rx-02' }),
      ],
      result(),
    )
    expect(groups[0]!.headline.rule_code).toBe('SALT_DIFFERENT_CLASS')
  })

  it('nothing is dropped: every finding survives in its group', () => {
    const findings = [
      finding('BILL_NOT_PRESCRIBED', 'critical', { bill: 'bill-05' }),
      finding('SCHEDULE_H_UNBACKED', 'critical', { bill: 'bill-05' }),
      finding('FORM_MISMATCH', 'warning', { rx: 'rx-01' }),
      finding('DATE_ANOMALY', 'warning'),
    ]
    const groups = groupByItem(findings, result())
    expect(groups.flatMap((g) => g.findings)).toHaveLength(findings.length)
  })

  it('counts items, not findings', () => {
    const groups = groupByItem(
      [
        finding('BILL_NOT_PRESCRIBED', 'critical', { bill: 'bill-05' }),
        finding('SCHEDULE_H_UNBACKED', 'critical', { bill: 'bill-05' }),
        finding('FORM_MISMATCH', 'warning', { rx: 'rx-01' }),
        finding('BRAND_SUBSTITUTION', 'info', { rx: 'rx-02' }),
      ],
      result(),
    )
    // Three rows, but only two of them are discrepancies.
    expect(groups).toHaveLength(3)
    expect(discrepancyCount(groups)).toBe(2)
    expect(criticalCount(groups)).toBe(1)
  })

  it('an info-only group is a row but not a discrepancy', () => {
    const groups = groupByItem([finding('BRAND_SUBSTITUTION', 'info', { rx: 'rx-01' })], result())
    expect(groups).toHaveLength(1)
    expect(groups[0]!.isDiscrepancy).toBe(false)
    expect(discrepancyCount(groups)).toBe(0)
  })
})

describe('hiding non-medicine noise from the Analysis view', () => {
  it('removes "billed but not prescribed" from a confirmed non-medicine', () => {
    const kept = hideNonMedicineNoise([
      finding('BILL_NOT_PRESCRIBED', 'info', { bill: 'bill-08' }),
      finding('NON_MEDICINE_ITEM', 'info', { bill: 'bill-08' }),
    ])
    expect(kept).toHaveLength(0)
  })

  it('leaves an UNCLASSIFIED line alone — we do not know what it is', () => {
    const findings = [finding('BILL_NOT_PRESCRIBED', 'critical', { bill: 'bill-09' })]
    expect(hideNonMedicineNoise(findings)).toEqual(findings)
  })

  it('never hides anything else against the same line', () => {
    const kept = hideNonMedicineNoise([
      finding('BILL_NOT_PRESCRIBED', 'info', { bill: 'bill-08' }),
      finding('NON_MEDICINE_ITEM', 'info', { bill: 'bill-08' }),
      finding('LINE_TOTAL_MISMATCH', 'warning', { bill: 'bill-08' }),
    ])
    expect(kept.map((f) => f.rule_code)).toEqual(['LINE_TOTAL_MISMATCH'])
  })

  it('never touches a different line', () => {
    const kept = hideNonMedicineNoise([
      finding('NON_MEDICINE_ITEM', 'info', { bill: 'bill-08' }),
      finding('BILL_NOT_PRESCRIBED', 'critical', { bill: 'bill-02' }),
    ])
    expect(kept.map((f) => f.rule_code)).toEqual(['BILL_NOT_PRESCRIBED'])
  })

  it('never touches document-level findings', () => {
    const findings = [
      finding('NON_MEDICINE_ITEM', 'info', { bill: 'bill-08' }),
      finding('DATE_ANOMALY', 'warning'),
    ]
    expect(hideNonMedicineNoise(findings).map((f) => f.rule_code)).toEqual(['DATE_ANOMALY'])
  })

  it('is a no-op when nothing is a non-medicine', () => {
    const findings = [
      finding('BILL_NOT_PRESCRIBED', 'critical', { bill: 'bill-01' }),
      finding('STRENGTH_MISMATCH', 'critical', { rx: 'rx-01' }),
    ]
    expect(hideNonMedicineNoise(findings)).toBe(findings)
  })

  it('the header count follows the filtered list', () => {
    const all = [
      finding('STRENGTH_MISMATCH', 'critical', { rx: 'rx-01' }),
      finding('BILL_NOT_PRESCRIBED', 'info', { bill: 'bill-08' }),
      finding('NON_MEDICINE_ITEM', 'info', { bill: 'bill-08' }),
    ]
    expect(discrepancyCount(groupByItem(all, result()))).toBe(1)
    expect(discrepancyCount(groupByItem(hideNonMedicineNoise(all), result()))).toBe(1)
    expect(groupByItem(hideNonMedicineNoise(all), result())).toHaveLength(1)
  })
})
