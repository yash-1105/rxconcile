/**
 * Group findings by the item they concern, so one medicine is one row.
 *
 * Alprax produced two rows — "was billed but was not prescribed" and "is a
 * prescription-only medicine with nothing backing it" — which is the same fact
 * told twice, and made the summary count items twice over.
 *
 * Two rules earn their place here:
 *
 * **A matched pair is one item.** A finding may carry a prescribed ref, a
 * billed ref, or both. Keying naively on either would split one medicine across
 * two groups, so refs are folded together through the pairs the engine already
 * reported.
 *
 * **SCHEDULE_H_UNBACKED is always the headline.** A prescription-only medicine
 * dispensed with nothing behind it is the most consequential thing this system
 * detects, and it is exactly what a naive merge buries under a more generic
 * line. It is pinned, not ranked.
 */

import type { Finding, ReconciliationResult, Severity } from '../types/api'

/** Never allowed to become the hidden half of a "+1 more". */
const PINNED = 'SCHEDULE_H_UNBACKED'

/**
 * Tie-break within one severity: the more specific message wins.
 *
 * "Alprax is a prescription-only medicine with nothing backing it" tells a
 * reviewer more than "ALPRAX was billed but was not prescribed", and both are
 * critical.
 */
const SPECIFICITY: readonly string[] = [
  PINNED,
  'SALT_DIFFERENT_CLASS',
  'STRENGTH_MISMATCH',
  'DUPLICATE_THERAPY',
  'PANEL_PARTIAL',
  'TEST_DUPLICATE',
  'FORM_MISMATCH',
  'QUANTITY_SHORT',
  'QUANTITY_EXCESS',
  'BRAND_SUBSTITUTION',
  'TEST_NOT_PRESCRIBED',
  'TEST_NOT_BILLED',
  'BILL_NOT_PRESCRIBED',
  'RX_NOT_BILLED',
]

const SEVERITY_RANK: Record<Severity, number> = { critical: 0, warning: 1, info: 2 }

export interface FindingGroup {
  key: string
  /** The finding whose message heads the row. */
  headline: Finding
  /** Every finding in the group, headline first. Nothing is dropped. */
  findings: Finding[]
  severity: Severity
  /** True when this group counts towards the discrepancy total. */
  isDiscrepancy: boolean
}

/** Fold a matched pair's two ids onto one key. */
function canonicalRefs(result: ReconciliationResult): Map<string, string> {
  const canonical = new Map<string, string>()
  for (const pair of [...result.matched_pairs, ...(result.matched_tests ?? [])]) {
    canonical.set(pair.prescribed_id, pair.prescribed_id)
    canonical.set(pair.billed_id, pair.prescribed_id)
  }
  return canonical
}

function rank(finding: Finding): [number, number] {
  const specificity = SPECIFICITY.indexOf(finding.rule_code)
  return [
    SEVERITY_RANK[finding.severity],
    specificity === -1 ? SPECIFICITY.length : specificity,
  ]
}

/** Severity first, then specificity. */
function compare(a: Finding, b: Finding): number {
  const [aSeverity, aSpecific] = rank(a)
  const [bSeverity, bSpecific] = rank(b)
  return aSeverity - bSeverity || aSpecific - bSpecific
}

export function groupByItem(findings: Finding[], result: ReconciliationResult): FindingGroup[] {
  const canonical = canonicalRefs(result)
  const groups = new Map<string, Finding[]>()

  findings.forEach((finding, index) => {
    const ref = finding.prescribed_ref ?? finding.billed_ref
    // Document-level findings concern no item, so each stays its own row rather
    // than being merged into something it is not about.
    const key = ref === null || ref === undefined ? `doc-${index}` : (canonical.get(ref) ?? ref)
    const existing = groups.get(key)
    if (existing) existing.push(finding)
    else groups.set(key, [finding])
  })

  const out: FindingGroup[] = []
  for (const [key, members] of groups) {
    const ordered = [...members].sort(compare)
    // Pinned, not ranked: a Schedule H finding heads its row whatever else is
    // in it.
    const pinnedIndex = ordered.findIndex((f) => f.rule_code === PINNED)
    if (pinnedIndex > 0) {
      const [pinned] = ordered.splice(pinnedIndex, 1)
      ordered.unshift(pinned!)
    }
    const headline = ordered[0]!
    const severity = ordered.reduce<Severity>(
      (worst, f) => (SEVERITY_RANK[f.severity] < SEVERITY_RANK[worst] ? f.severity : worst),
      'info',
    )
    out.push({
      key,
      headline,
      findings: ordered,
      severity,
      isDiscrepancy: severity !== 'info',
    })
  }

  return out.sort(
    (a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || compare(a.headline, b.headline),
  )
}

/** Items with a problem, not raw findings. Two findings on one medicine are one. */
export function discrepancyCount(groups: FindingGroup[]): number {
  return groups.filter((group) => group.isDiscrepancy).length
}

/** Of those, the ones that are serious. Counted the same way, by item. */
export function criticalCount(groups: FindingGroup[]): number {
  return groups.filter((group) => group.severity === 'critical').length
}
