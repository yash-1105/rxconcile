import { useEffect, useMemo, useState } from 'react'
import { getScan, listScans } from '../api/client'
import { EmptyState, PageHeader } from '../components/Shell'
import { SpineMark } from '../components/Spine'
import {
  applyFilters,
  claimAmountOf,
  formatDate,
  formatTime,
  QUEUE_FILTERS,
  REVIEW_LABEL,
  VERDICT_LABEL,
  verdictState,
  type ScanFilters,
} from '../lib/scans'
import type { ReviewStatus, ScanDetail, ScanSummary, Verdict } from '../types/api'

const VERDICTS: readonly Verdict[] = [
  'match',
  'match_with_warnings',
  'mismatch',
  'inconclusive',
]

const STATUSES: readonly ReviewStatus[] = ['submitted', 'under_review', 'reviewed']

const STATUS_STYLE: Record<ReviewStatus, string> = {
  submitted: 'border-amber-300 bg-amber-50 text-amber-900',
  under_review: 'border-sky-300 bg-sky-50 text-sky-900',
  reviewed: 'border-emerald-300 bg-emerald-50 text-emerald-900',
}

function StatusChip({ status }: { status: ReviewStatus }) {
  return (
    <span
      className={`inline-block rounded border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${STATUS_STYLE[status]}`}
    >
      {REVIEW_LABEL[status]}
    </span>
  )
}

/**
 * Whether the employee attested to this claim.
 *
 * Flagged rather than merely absent. An uncertified submission is one nobody
 * has signed for, and a reviewer deciding it has to know that before they
 * decide it — not after, from a field they did not think to look at.
 */
function Certification({ scan }: { scan: ScanSummary }) {
  if (scan.certified_by_employee) {
    return (
      <span className="t-small text-muted" title={scan.certified_at ?? undefined}>
        Certified
      </span>
    )
  }
  return (
    <span className="inline-block rounded border border-red-300 bg-red-50 px-2 py-0.5 text-xs font-medium whitespace-nowrap text-red-800">
      Not certified
    </span>
  )
}

/**
 * The reviewer's primary screen: claims waiting to be decided.
 *
 * It opens filtered to `submitted`, because the first thing a reviewer needs
 * is the work, not the archive. The filter is a visible control set to a
 * value, not a hidden restriction — clearing it shows everything, and the
 * count beside it always says how much of the whole is on screen.
 *
 * Opening a row shows the full result. Nothing is reduced there: this screen
 * decides which claim, not how much of it a reviewer may see.
 */
export function Queue({
  onOpen,
  onError,
}: {
  onOpen: (detail: ScanDetail) => void
  onError?: (message: string) => void
}) {
  const [scans, setScans] = useState<ScanSummary[] | null>(null)
  const [filters, setFilters] = useState<ScanFilters>(QUEUE_FILTERS)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listScans()
      .then(setScans)
      .catch((caught: unknown) => {
        setScans([])
        setError(caught instanceof Error ? caught.message : 'Could not load the queue.')
      })
  }, [])

  // Newest first. A queue ordered oldest-first buries today's work under a
  // backlog, and the backlog is what the status filter is for.
  const ordered = useMemo(
    () => [...(scans ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [scans],
  )
  const visible = useMemo(() => applyFilters(ordered, filters), [ordered, filters])
  const waiting = useMemo(
    () => ordered.filter((scan) => scan.review_status === 'submitted').length,
    [ordered],
  )

  const open = (scan: ScanSummary) => {
    getScan(scan.id)
      .then(onOpen)
      .catch(() => {
        setError('Could not open that claim.')
        onError?.('Could not open that claim.')
      })
  }

  if (scans === null) {
    return (
      <>
        <PageHeader title="Review queue" />
        <p className="t-small text-muted">Loading…</p>
      </>
    )
  }

  return (
    <>
      <PageHeader
        title="Review queue"
        lede={
          waiting === 0
            ? 'Nothing is waiting to be reviewed.'
            : `${waiting} claim${waiting === 1 ? '' : 's'} waiting to be reviewed.`
        }
      />

      {error ? <p className="t-small mb-4 text-flag">{error}</p> : null}

      {scans.length === 0 ? (
        <EmptyState
          title="No claims yet"
          body="Submitted claims arrive here for review, newest first."
        />
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-end gap-3">
            <label className="block">
              <span className="t-micro block text-muted">Status</span>
              <select
                value={filters.review}
                onChange={(e) =>
                  setFilters({ ...filters, review: e.target.value as ReviewStatus | 'all' })
                }
                className="t-small mt-1 rounded bg-surface px-2.5 py-1.5 text-ink"
              >
                <option value="all">All</option>
                {STATUSES.map((status) => (
                  <option key={status} value={status}>
                    {REVIEW_LABEL[status]}
                  </option>
                ))}
              </select>
            </label>

            <label className="block">
              <span className="t-micro block text-muted">Verdict</span>
              <select
                value={filters.verdict}
                onChange={(e) =>
                  setFilters({ ...filters, verdict: e.target.value as Verdict | 'all' })
                }
                className="t-small mt-1 rounded bg-surface px-2.5 py-1.5 text-ink"
              >
                <option value="all">All</option>
                {VERDICTS.map((verdict) => (
                  <option key={verdict} value={verdict}>
                    {VERDICT_LABEL[verdict]}
                  </option>
                ))}
              </select>
            </label>

            <label className="block">
              <span className="t-micro block text-muted">Employee name</span>
              <input
                type="search"
                value={filters.name}
                onChange={(e) => setFilters({ ...filters, name: e.target.value })}
                placeholder="Any name"
                className="t-small mt-1 rounded bg-surface px-2.5 py-1.5 text-ink placeholder:text-ink-400"
              />
            </label>

            <label className="block">
              <span className="t-micro block text-muted">Employee number</span>
              <input
                type="search"
                value={filters.number}
                onChange={(e) => setFilters({ ...filters, number: e.target.value })}
                placeholder="Any number"
                className="t-small mt-1 rounded bg-surface px-2.5 py-1.5 text-ink placeholder:text-ink-400"
              />
            </label>

            <label className="block">
              <span className="t-micro block text-muted">Condition</span>
              <input
                type="search"
                value={filters.condition}
                onChange={(e) => setFilters({ ...filters, condition: e.target.value })}
                placeholder="Any condition"
                className="t-small mt-1 rounded bg-surface px-2.5 py-1.5 text-ink placeholder:text-ink-400"
              />
            </label>

            <label className="block">
              <span className="t-micro block text-muted">From</span>
              <input
                type="date"
                value={filters.from}
                onChange={(e) => setFilters({ ...filters, from: e.target.value })}
                className="t-small mt-1 rounded bg-surface px-2.5 py-1.5 text-ink"
              />
            </label>
            <label className="block">
              <span className="t-micro block text-muted">To</span>
              <input
                type="date"
                value={filters.to}
                onChange={(e) => setFilters({ ...filters, to: e.target.value })}
                className="t-small mt-1 rounded bg-surface px-2.5 py-1.5 text-ink"
              />
            </label>

            <button
              type="button"
              onClick={() => setFilters(QUEUE_FILTERS)}
              className="t-small text-muted underline decoration-ink-300 underline-offset-4 hover:text-ink"
            >
              Reset
            </button>
            <span className="t-small ml-auto text-muted">
              {visible.length} of {ordered.length}
            </span>
          </div>

          <div className="overflow-x-auto rounded bg-surface">
            <table className="w-full min-w-[64rem] border-collapse">
              <thead>
                <tr className="border-b border-ink-200 text-left">
                  {[
                    'Submitted',
                    'Employee',
                    'Number',
                    'Condition',
                    'Verdict',
                    'Discrepancies',
                    'Claim',
                    'Certification',
                    'Status',
                  ].map((head) => (
                    <th key={head} className="t-micro px-4 py-3 text-muted">
                      {head}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visible.map((scan) => {
                  const claim = claimAmountOf(scan)
                  return (
                    <tr
                      key={scan.id}
                      onClick={() => open(scan)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault()
                          open(scan)
                        }
                      }}
                      tabIndex={0}
                      role="button"
                      aria-label={`Review the claim from ${scan.employee_name} submitted on ${formatDate(scan.created_at)}`}
                      className="cursor-pointer border-b border-ink-100 last:border-b-0 hover:bg-paper focus-visible:bg-paper"
                    >
                      <td className="t-small px-4 py-3.5 text-muted whitespace-nowrap">
                        {formatDate(scan.created_at)}
                        <span className="t-small ml-2 text-ink-400">
                          {formatTime(scan.created_at)}
                        </span>
                      </td>
                      <td className="t-small px-4 py-3.5 font-medium text-ink">
                        {scan.employee_name}
                      </td>
                      <td className="t-data px-4 py-3.5 text-muted">{scan.employee_number}</td>
                      <td className="t-small px-4 py-3.5 text-muted" title={scan.description ?? ''}>
                        {scan.condition ?? '—'}
                      </td>
                      <td className="px-4 py-3.5">
                        <span className="flex items-center gap-2.5">
                          <span className="flex w-3.5 justify-center">
                            <SpineMark state={verdictState(scan.verdict)} />
                          </span>
                          <span className="t-small whitespace-nowrap text-ink">
                            {VERDICT_LABEL[scan.verdict]}
                          </span>
                        </span>
                      </td>
                      <td className="t-data px-4 py-3.5 text-ink">
                        {scan.discrepancy_count}
                        {scan.checks_unavailable_count > 0 ? (
                          <span
                            className="t-small ml-2 text-muted"
                            title="Checks that could not run — not discrepancies"
                          >
                            +{scan.checks_unavailable_count} not checked
                          </span>
                        ) : null}
                      </td>
                      {/* Blank until a reviewer has decided. See `claimAmountOf`. */}
                      <td className="t-data px-4 py-3.5 whitespace-nowrap text-ink">
                        {claim ?? (
                          <span className="text-ink-400" title="No amount decided yet">
                            —
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3.5">
                        <Certification scan={scan} />
                      </td>
                      <td className="px-4 py-3.5">
                        <StatusChip status={scan.review_status} />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {visible.length === 0 ? (
            <p className="t-small mt-4 text-muted">
              No claims match these filters.{' '}
              <button
                type="button"
                onClick={() => setFilters({ ...QUEUE_FILTERS, review: 'all' })}
                className="underline decoration-ink-300 underline-offset-4 hover:text-ink"
              >
                Show every status
              </button>
            </p>
          ) : null}
        </>
      )}
    </>
  )
}
