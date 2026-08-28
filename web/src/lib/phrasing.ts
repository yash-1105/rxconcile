/**
 * Turns findings into sentences a person would write.
 *
 * Presentation only. Nothing here changes a verdict, a severity or a number —
 * it decides how a computed result is worded, and which of them lead.
 */

import type {
  BilledItem,
  Finding,
  PharmacyBill,
  PrescribedItem,
  Prescription,
  ReconciliationResult,
  Severity,
} from '../types/api'

/** Findings that describe something genuinely wrong with the dispensing. */
const DISCREPANCY_CODES = new Set([
  'STRENGTH_MISMATCH',
  'RX_NOT_BILLED',
  'BILL_NOT_PRESCRIBED',
  'SCHEDULE_H_UNBACKED',
  'SALT_DIFFERENT_CLASS',
  'ITEM_COUNT_UNSTABLE',
  'FORM_MISMATCH',
  'QUANTITY_SHORT',
  'QUANTITY_EXCESS',
  'DUPLICATE_THERAPY',
  'PATIENT_NAME_MISMATCH',
  'DATE_ANOMALY',
  'TEST_NOT_BILLED',
  'TEST_NOT_PRESCRIBED',
  'PANEL_PARTIAL',
  'TEST_DUPLICATE',
])

/** Findings that say a check was attempted but could not conclude. */
const UNVERIFIED_CODES = new Set([
  'QUANTITY_AMBIGUOUS',
  'STRENGTH_UNIT_UNSTATED',
  'TEST_UNRESOLVED',
])

/** Findings that say a check never ran at all. */
const NOT_RUN_CODES = new Set(['CHECK_UNAVAILABLE'])

/** Worth telling the client, but not a problem. */
const NOTED_CODES = new Set(['BRAND_SUBSTITUTION'])

export interface Grouped {
  discrepancies: Finding[]
  unverified: Finding[]
  notRun: Finding[]
  noted: Finding[]
  quality: Finding[]
}

export function groupFindings(findings: Finding[]): Grouped {
  const severityRank: Record<Severity, number> = { critical: 0, warning: 1, info: 2 }
  const discrepancies = findings
    .filter((f) => DISCREPANCY_CODES.has(f.rule_code))
    .sort((a, b) => severityRank[a.severity] - severityRank[b.severity])
  return {
    discrepancies,
    unverified: findings.filter((f) => UNVERIFIED_CODES.has(f.rule_code)),
    notRun: findings.filter((f) => NOT_RUN_CODES.has(f.rule_code)),
    noted: findings.filter((f) => NOTED_CODES.has(f.rule_code)),
    quality: findings.filter(
      (f) =>
        !DISCREPANCY_CODES.has(f.rule_code) &&
        !UNVERIFIED_CODES.has(f.rule_code) &&
        !NOT_RUN_CODES.has(f.rule_code) &&
        !NOTED_CODES.has(f.rule_code),
    ),
  }
}

function num(value: unknown): string {
  return typeof value === 'number' ? String(value) : String(value ?? '')
}

function strengthOf(item: PrescribedItem | BilledItem | undefined): string | null {
  if (!item || item.strength_value === null) return null
  return `${item.strength_value}${item.strength_unit ?? ''}`
}

function nameOf(item: PrescribedItem | BilledItem | undefined, fallback = 'This item'): string {
  return item?.drug_name ?? fallback
}

/**
 * A sentence describing one finding, in the terms a pharmacist would use.
 *
 * Falls back to the engine's own message, which is already plain English, so an
 * unrecognised rule code degrades to something readable rather than to a blank.
 */
export function phrase(
  finding: Finding,
  prescription: Prescription,
  bill: PharmacyBill,
): string {
  const rx = prescription.items.find((i) => i.item_id === finding.prescribed_ref)
  const bl = bill.items.find((i) => i.item_id === finding.billed_ref)
  const detail = finding.detail

  switch (finding.rule_code) {
    case 'STRENGTH_MISMATCH':
      return `${nameOf(rx)} — prescribed ${strengthOf(rx) ?? 'an unstated strength'}, billed ${
        strengthOf(bl) ?? 'an unstated strength'
      }`
    case 'RX_NOT_BILLED':
      // A softened finding must not be worded as a confident one. Saying
      // "does not appear on the bill" under a banner explaining the bill was
      // never supplied contradicts the banner and reads as an accusation.
      if (detail['lab_only_bill'] === true) {
        return `${nameOf(rx)} was not assessed — this bill carries no medicines`
      }
      return detail['identified'] === false
        ? 'A prescribed line could not be read, so whether it was dispensed is unknown'
        : `${nameOf(rx)} was prescribed but does not appear on the bill`
    case 'BILL_NOT_PRESCRIBED':
      return detail['identified'] === false
        ? 'A billed line could not be read, so whether it was prescribed is unknown'
        : `${nameOf(bl)} was billed but was not prescribed`
    case 'SCHEDULE_H_UNBACKED':
      return `${String(
        detail['brand'] ?? nameOf(bl),
      )} is a prescription-only medicine and has nothing backing it on the prescription`
    case 'FORM_MISMATCH':
      return `${nameOf(rx)} — prescribed as ${String(detail['expected'])}, billed as ${String(
        detail['found'],
      )}`
    case 'QUANTITY_SHORT':
      return `${nameOf(rx)} — ${num(detail['billed_units'])} dispensed against ${num(
        detail['expected_units'],
      )} expected for the course`
    case 'QUANTITY_EXCESS':
      return `${nameOf(rx)} — ${num(detail['billed_units'])} dispensed, more than the ${num(
        detail['expected_units'],
      )} the course requires`
    case 'DUPLICATE_THERAPY': {
      const refs = detail['billed_refs']
      const count = Array.isArray(refs) ? refs.length : 2
      return `${String(detail['salt'])} appears on ${count} separate billed lines`
    }
    case 'PATIENT_NAME_MISMATCH':
      return `The patient name differs — “${String(
        detail['prescription_name'],
      )}” on the prescription, “${String(detail['bill_name'])}” on the bill`
    case 'DATE_ANOMALY': {
      const days = Number(detail['days_between'] ?? 0)
      return days < 0
        ? `The bill is dated ${Math.abs(days)} days before the prescription`
        : `The bill is dated ${days} days after the prescription`
    }
    case 'SALT_DIFFERENT_CLASS':
      return `${nameOf(rx)} and ${nameOf(
        bl,
      )} are different kinds of medicine — more likely a misreading than a substitution`
    case 'ITEM_COUNT_UNSTABLE':
      return `Some lines on the ${String(
        detail['document'],
      )} were not read consistently, so this comparison may be incomplete`
    case 'BRAND_SUBSTITUTION':
      return `${String(detail['billed_brand'])} was dispensed instead of ${String(
        detail['prescribed_brand'],
      )} — same medicine, different brand`
    case 'QUANTITY_AMBIGUOUS':
      return `${nameOf(rx)} — the bill shows ${num(
        detail['billed_quantity'],
      )} against a pack of ${num(detail['units_per_pack'])}`
    case 'STRENGTH_UNIT_UNSTATED':
      return `${nameOf(rx)} — both documents show the same number, but only one prints a unit`
    case 'TEST_NOT_BILLED':
      return detail['softened_because']
        ? `${String(detail['resolved_as'])} was ordered, but nothing on the bill could be matched to it`
        : `${String(detail['resolved_as'])} was ordered but does not appear on the bill`
    case 'TEST_NOT_PRESCRIBED':
      return detail['softened_because']
        ? `${String(detail['resolved_as'])} was billed, and what was ordered could not be established`
        : `${String(detail['resolved_as'])} was billed but was not among the tests ordered`
    case 'PANEL_PARTIAL': {
      const missing = (detail['missing_components'] as string[] | undefined) ?? []
      return `${String(detail['panel'])} was ordered but the bill is missing ${missing.join(', ')}`
    }
    case 'TEST_DUPLICATE':
      return detail['quantity']
        ? `${String(detail['test'])} is billed with a quantity of ${num(detail['quantity'])}`
        : `${String(detail['test'])} is billed more than once`
    case 'TEST_UNRESOLVED':
      return `“${String(detail['written'])}” on the ${String(
        detail['side'],
      )} is not a test this build recognises, so it could not be checked`
    case 'CHECK_UNAVAILABLE':
      return `${String(detail['check'])} — needs ${
        (detail['missing'] as string[] | undefined)?.join(', ') ?? 'missing input'
      }`
    default:
      return finding.message
  }
}

export interface Headline {
  title: string
  supporting: string
  tone: 'clear' | 'warning' | 'problem' | 'unknown'
}

/**
 * The one line a client reads first.
 *
 * "Everything matches" is reserved for a run where every check actually ran. If
 * any check could not run, the wording says so — a check that did not run must
 * never read as a check that passed.
 */
export function headline(result: ReconciliationResult, grouped: Grouped): Headline {
  const count = grouped.discrepancies.length
  const notRun = grouped.notRun.length + grouped.unverified.length

  if (result.verdict === 'inconclusive') {
    return {
      title: 'Could not read reliably',
      supporting:
        'These documents could not be read consistently enough to compare. This is not a ' +
        'finding that they match, and not a finding that they differ. Review them by hand.',
      tone: 'unknown',
    }
  }
  if (count === 0) {
    return notRun > 0
      ? {
          title: 'No discrepancies found in the checks that ran',
          supporting: `Everything that could be compared matched. ${notRun} ${
            notRun === 1 ? 'check' : 'checks'
          } could not be completed, listed below.`,
          tone: 'warning',
        }
      : {
          title: 'Everything matches',
          supporting:
            'Every prescribed item appears on the bill at the right strength and form, and ' +
            'every check completed.',
          tone: 'clear',
        }
  }
  const criticals = grouped.discrepancies.filter((f) => f.severity === 'critical').length
  return {
    title: `${count} ${count === 1 ? 'discrepancy' : 'discrepancies'} found`,
    supporting:
      criticals > 0
        ? `${criticals} of them ${criticals === 1 ? 'is' : 'are'} serious: the bill does not ` +
          'match what was prescribed. Each is listed below with the source line.'
        : 'None are serious, but each is worth checking against the source documents.',
    tone: criticals > 0 ? 'problem' : 'warning',
  }
}

// ---------------------------------------------------------------------------
// Table remarks
// ---------------------------------------------------------------------------

/**
 * The plain-English reason a table row is flagged.
 *
 * Presentation only: it reads the rule codes the engine already emitted and
 * words them. It never decides anything. Rows with nothing wrong get an empty
 * remark rather than the word "OK", so the eye skips them.
 */
export function remark(codes: string[], findings: Finding[]): string {
  const has = (code: string) => codes.includes(code)
  if (has('BILL_NOT_PRESCRIBED')) {
    const found = findings.find((f) => f.rule_code === 'BILL_NOT_PRESCRIBED')
    return found?.detail['identified'] === false
      ? 'Billed line could not be read'
      : 'Not found in prescription'
  }
  if (has('RX_NOT_BILLED')) {
    const found = findings.find((f) => f.rule_code === 'RX_NOT_BILLED')
    if (found?.detail['lab_only_bill'] === true) return 'Not assessed — no pharmacy bill supplied'
    if (found?.detail['identified'] === false) return 'Prescribed line could not be read'
    return 'Not bought'
  }
  if (has('SALT_DIFFERENT_CLASS')) return 'Different kind of medicine — likely a misreading'
  if (has('STRENGTH_MISMATCH')) return 'Strength differs from the prescription'
  if (has('SCHEDULE_H_UNBACKED')) return 'Prescription-only medicine with nothing backing it'
  if (has('BRAND_SUBSTITUTION')) return 'Alternate medicine bought — same salt'
  if (has('FORM_MISMATCH')) return 'Dispensed in a different form'
  if (has('DUPLICATE_THERAPY')) return 'Same salt appears on more than one line'
  if (has('QUANTITY_SHORT')) return 'Less dispensed than the course requires'
  if (has('QUANTITY_EXCESS')) return 'More dispensed than the course requires'
  if (has('QUANTITY_AMBIGUOUS')) return 'Quantity not verifiable'
  if (has('STRENGTH_UNIT_UNSTATED')) return 'Strength unit not printed — not verifiable'
  return ''
}

/** The same, for a lab test row. */
export function testRemark(codes: string[], findings: Finding[]): string {
  const has = (code: string) => codes.includes(code)
  const softened = (code: string) =>
    Boolean(findings.find((f) => f.rule_code === code)?.detail['softened_because'])
  if (has('TEST_NOT_PRESCRIBED')) {
    return softened('TEST_NOT_PRESCRIBED')
      ? 'Billed — what was ordered could not be established'
      : 'Not found in prescription'
  }
  if (has('TEST_NOT_BILLED')) {
    return softened('TEST_NOT_BILLED') ? 'Not assessed — no lab bill supplied' : 'Not done'
  }
  if (has('PANEL_PARTIAL')) {
    const found = findings.find((f) => f.rule_code === 'PANEL_PARTIAL')
    const missing = (found?.detail['missing_components'] as string[] | undefined) ?? []
    return `Panel billed incompletely — missing ${missing.join(', ')}`
  }
  if (has('TEST_DUPLICATE')) return 'Billed more than once'
  if (has('TEST_UNRESOLVED')) return 'Not a test this build recognises — not verifiable'
  return ''
}

// ---------------------------------------------------------------------------
// Document completeness
// ---------------------------------------------------------------------------

export interface DocumentGap {
  /** What the reader must do something about. */
  title: string
  detail: string
  /** How many lines went unassessed because of it. */
  count: number
}

/**
 * The highest-consequence "we could not check" in the product.
 *
 * If a lab-only bill is reconciled against a prescription carrying medicines,
 * every one of those medicines is unassessed — and a screen reporting no
 * problems with six medicines nobody examined is worse than no screen. This is
 * read off findings the engine already emitted and belongs at the TOP of the
 * results, never inside the collapsed footnotes.
 */
export function documentGaps(result: ReconciliationResult): DocumentGap[] {
  const gaps: DocumentGap[] = []

  const unassessedMedicines = result.findings.filter(
    (f) => f.rule_code === 'RX_NOT_BILLED' && f.detail['lab_only_bill'] === true,
  )
  if (unassessedMedicines.length > 0) {
    gaps.push({
      title: 'The pharmacy bill was not supplied',
      detail:
        `This bill carries only lab tests and no medicines at all, so ${unassessedMedicines.length} ` +
        `prescribed ${unassessedMedicines.length === 1 ? 'medicine was' : 'medicines were'} not ` +
        'assessed. Nothing below says they were dispensed correctly — they were not checked. ' +
        'Upload the pharmacy bill to compare them.',
      count: unassessedMedicines.length,
    })
  }

  const unassessedTests = result.findings.filter(
    (f) =>
      f.rule_code === 'TEST_NOT_BILLED' &&
      typeof f.detail['softened_because'] === 'string' &&
      (f.detail['softened_because'] as string).includes('only medicines'),
  )
  if (unassessedTests.length > 0) {
    gaps.push({
      title: 'The lab bill was not supplied',
      detail:
        `This bill carries only medicines and no lab lines, so ${unassessedTests.length} ordered ` +
        `${unassessedTests.length === 1 ? 'test was' : 'tests were'} not assessed. Lab work is ` +
        'commonly billed on a separate document.',
      count: unassessedTests.length,
    })
  }

  const ordersUnreadable = result.findings.find(
    (f) =>
      f.rule_code === 'CHECK_UNAVAILABLE' &&
      (f.detail['missing'] as string[] | undefined)?.some((m) =>
        m.includes('readable list of ordered investigations'),
      ),
  )
  if (ordersUnreadable) {
    gaps.push({
      title: 'The investigations ordered could not be read',
      detail:
        'The prescription has an investigations section that could not be read, so what was ' +
        'ordered is unknown. Billed tests below are neither confirmed as ordered nor reported ' +
        'as unordered.',
      count: (result.bill.tests ?? []).length,
    })
  }

  return gaps
}
