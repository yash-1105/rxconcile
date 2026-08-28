import { describe, expect, it } from 'vitest'
import { statusFrom } from './rowStatus'
import type { Finding, Severity } from '../types/api'

function finding(rule_code: string, severity: Severity): Finding {
  return { rule_code, severity, message: '', prescribed_ref: null, billed_ref: null, detail: {} }
}

const BRAND = finding('BRAND_SUBSTITUTION', 'info')
const AMBIGUOUS = finding('QUANTITY_AMBIGUOUS', 'info')
const FORM = finding('FORM_MISMATCH', 'warning')
const STRENGTH = finding('STRENGTH_MISMATCH', 'critical')
const UNAVAILABLE = finding('CHECK_UNAVAILABLE', 'info')

describe('row status precedence: critical > warning > matched > partial > nothing', () => {
  it('a critical leads, even alongside warnings and unverifiable checks', () => {
    expect(statusFrom([AMBIGUOUS, FORM, STRENGTH], { paired: true }).state).toBe('problem')
  })

  it('a warning leads over a match and over unverifiable checks', () => {
    expect(statusFrom([AMBIGUOUS, FORM], { paired: true }).state).toBe('warning')
  })

  it('THE DEFECT: a clean substitution with one unverifiable check is matched', () => {
    // This read as NOT CHECKED before: the weakest check decided the row.
    const status = statusFrom([BRAND, AMBIGUOUS], { paired: true })
    expect(status.state).toBe('clean')
    expect(status.partial).toBe(true)
  })

  it('a paired row with nothing against it is matched and not partial', () => {
    expect(statusFrom([], { paired: true })).toEqual({ state: 'clean', partial: false })
  })

  it('an unverifiable check marks a row without downgrading it', () => {
    expect(statusFrom([AMBIGUOUS], { paired: true })).toEqual({ state: 'clean', partial: true })
    expect(statusFrom([UNAVAILABLE], { paired: true })).toEqual({ state: 'clean', partial: true })
  })

  it('the partial marker survives a real discrepancy on the same row', () => {
    expect(statusFrom([STRENGTH, AMBIGUOUS], { paired: true }).partial).toBe(true)
    expect(statusFrom([FORM, AMBIGUOUS], { paired: true }).partial).toBe(true)
  })

  it('NOT CHECKED is only for a row where nothing produced a result', () => {
    expect(statusFrom([], { paired: false })).toEqual({ state: 'unchecked', partial: false })
    expect(statusFrom([UNAVAILABLE], { paired: false }).state).toBe('unchecked')
  })

  it('an unmatched row still leads with its own discrepancy', () => {
    const notPrescribed = finding('BILL_NOT_PRESCRIBED', 'critical')
    expect(statusFrom([notPrescribed], { paired: false }).state).toBe('problem')
    const softened = finding('RX_NOT_BILLED', 'warning')
    expect(statusFrom([softened], { paired: false }).state).toBe('warning')
  })
})
