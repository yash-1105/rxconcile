import type { ScanSummary, Verdict } from '../types/api'

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

export function formatDate(iso: string): string {
  const date = new Date(iso)
  return date.toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' })
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

export interface ScanFilters {
  verdict: Verdict | 'all'
  employee: string
  from: string
  to: string
}

export const NO_FILTERS: ScanFilters = { verdict: 'all', employee: 'all', from: '', to: '' }

export function applyFilters(scans: ScanSummary[], filters: ScanFilters): ScanSummary[] {
  return scans.filter((scan) => {
    if (filters.verdict !== 'all' && scan.verdict !== filters.verdict) return false
    if (filters.employee !== 'all' && scan.user_email !== filters.employee) return false
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
