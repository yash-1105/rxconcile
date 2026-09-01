import type { ReviewStatus, ScanSummary, Verdict } from '../types/api'

export const REVIEW_LABEL: Record<ReviewStatus, string> = {
  submitted: 'Awaiting review',
  under_review: 'Under review',
  reviewed: 'Reviewed',
}

export const VERDICT_LABEL: Record<Verdict, string> = {
  match: 'Matches',
  match_with_warnings: 'Matches with warnings',
  mismatch: 'Discrepancies',
  inconclusive: 'Could not read',
}

/** Verdict maps to the same four marks used on the spine. */
export function verdictState(verdict: Verdict): 'clean' | 'warning' | 'problem' | 'unchecked' {
  if (verdict === 'match') return 'clean'
  if (verdict === 'match_with_warnings') return 'warning'
  if (verdict === 'mismatch') return 'problem'
  return 'unchecked'
}

/**
 * An amount, or nothing.
 *
 * `null` is not zero: a claim nobody has decided yet has no amount, and
 * printing 0.00 against it would say a reviewer looked and allowed nothing.
 */
export function formatMoney(amount: string | null, currency = 'INR'): string | null {
  if (amount === null) return null
  const value = Number(amount)
  if (!Number.isFinite(value)) return null
  return `${currency} ${value.toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

/**
 * The amount to show for a claim, which exists only once someone has decided.
 *
 * Until then the stored figure is a default the machine proposed and nobody
 * agreed to, so the queue shows nothing rather than presenting it as a claim.
 */
export function claimAmountOf(scan: ScanSummary): string | null {
  if (scan.review_status === 'submitted') return null
  return formatMoney(scan.claimed_amount, scan.currency)
}

export function formatDate(iso: string): string {
  const date = new Date(iso)
  return date.toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' })
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

export interface ScanFilters {
  verdict: Verdict | 'all'
  /** Where the claim has got to. The queue defaults this to `submitted`. */
  review: ReviewStatus | 'all'
  /** Free-text match on the condition typed on the scan. */
  condition: string
  employee: string
  /** Free-text match on the NAME typed on the scan, not the account. */
  name: string
  /** Free-text match on the employee number typed on the scan. */
  number: string
  from: string
  to: string
}

export const NO_FILTERS: ScanFilters = {
  verdict: 'all',
  review: 'all',
  condition: '',
  employee: 'all',
  name: '',
  number: '',
  from: '',
  to: '',
}

/**
 * What the review queue opens on: work that is waiting.
 *
 * A queue showing everything ever submitted is a history screen. The point of
 * the default is that the first thing a reviewer sees is the thing they have
 * to do, and the filter is visible and clearable so nothing is hidden by it.
 */
export const QUEUE_FILTERS: ScanFilters = { ...NO_FILTERS, review: 'submitted' }

export function applyFilters(scans: ScanSummary[], filters: ScanFilters): ScanSummary[] {
  return scans.filter((scan) => {
    if (filters.verdict !== 'all' && scan.verdict !== filters.verdict) return false
    if (filters.review !== 'all' && scan.review_status !== filters.review) return false
    const condition = filters.condition.trim().toLowerCase()
    if (condition && !(scan.condition ?? '').toLowerCase().includes(condition)) return false
    if (filters.employee !== 'all' && scan.user_email !== filters.employee) return false
    const name = filters.name.trim().toLowerCase()
    if (name && !scan.employee_name.toLowerCase().includes(name)) return false
    const number = filters.number.trim().toLowerCase()
    if (number && !scan.employee_number.toLowerCase().includes(number)) return false
    const day = scan.created_at.slice(0, 10)
    if (filters.from && day < filters.from) return false
    if (filters.to && day > filters.to) return false
    return true
  })
}

/**
 * Everything the dashboards show, derived from stored records only.
 *
 * Deliberately absent: accuracy, confidence and anything money-shaped. None of
 * those are measurable from what is stored, and a number that sounds impressive
 * without a basis is worse than no number.
 */
export interface Totals {
  scans: number
  discrepancies: number
  criticals: number
  checksUnavailable: number
  medianMs: number | null
}

export function totals(scans: ScanSummary[]): Totals {
  const durations = scans.map((s) => s.processing_ms).sort((a, b) => a - b)
  const middle = Math.floor(durations.length / 2)
  return {
    scans: scans.length,
    discrepancies: scans.reduce((sum, s) => sum + s.discrepancy_count, 0),
    criticals: scans.reduce((sum, s) => sum + s.critical_count, 0),
    checksUnavailable: scans.reduce((sum, s) => sum + s.checks_unavailable_count, 0),
    medianMs: durations.length ? (durations[middle] ?? null) : null,
  }
}

export function countBy<T extends string>(rows: readonly T[]): [T, number][] {
  const counts = new Map<T, number>()
  for (const row of rows) counts.set(row, (counts.get(row) ?? 0) + 1)
  return [...counts.entries()].sort((a, b) => b[1] - a[1])
}

/** Scans per day, oldest first, with empty days included so gaps are visible. */
export function perDay(scans: ScanSummary[]): { day: string; count: number }[] {
  if (scans.length === 0) return []
  const counts = new Map<string, number>()
  for (const scan of scans) {
    const day = scan.created_at.slice(0, 10)
    counts.set(day, (counts.get(day) ?? 0) + 1)
  }
  const days = [...counts.keys()].sort()
  const first = days[0]
  const last = days[days.length - 1]
  if (!first || !last) return []
  const out: { day: string; count: number }[] = []
  for (let d = new Date(first); d <= new Date(last); d.setDate(d.getDate() + 1)) {
    const key = d.toISOString().slice(0, 10)
    out.push({ day: key, count: counts.get(key) ?? 0 })
  }
  return out
}
