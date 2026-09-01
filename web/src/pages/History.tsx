import { useEffect, useMemo, useState } from 'react'
import {
  deleteScan,
  getScan,
  getSubmission,
  listScans,
  listSubmissions,
} from '../api/client'
import type { Session } from '../auth/session'
import { EmptyState, PageHeader } from '../components/Shell'
import { SpineMark } from '../components/Spine'
import {
  applyFilters,
  formatDate,
  formatTime,
  NO_FILTERS,
  VERDICT_LABEL,
  verdictState,
  type ScanFilters,
} from '../lib/scans'
import type {
  EmployeeScanDetail,
  EmployeeScanSummary,
  ScanDetail,
  ScanSummary,
  Verdict,
} from '../types/api'

const VERDICTS: readonly Verdict[] = [
  'match',
  'match_with_warnings',
  'mismatch',
  'inconclusive',
]

const REVIEW_LABEL: Record<string, string> = {
  submitted: 'Submitted',
  under_review: 'Under review',
  reviewed: 'Reviewed',
}

/**
 * A submitter's own history.
 *
 * Their submissions, not their results: date, condition, what they sent and
 * where it has got to. There is no verdict column because there is no verdict
 * in the response — the server sends the narrower shape, so the browser is
 * never trusted to leave it out.
 */
export function SubmissionHistory({
  onStart,
  onOpen,
}: {
  onStart: () => void
  onOpen: (detail: EmployeeScanDetail) => void
}) {
  const [rows, setRows] = useState<EmployeeScanSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listSubmissions()
      .then(setRows)
      .catch((caught: unknown) => {
        setRows([])
        setError(caught instanceof Error ? caught.message : 'Could not load your claims.')
      })
  }, [])

  const open = (id: number) => {
    getSubmission(id)
      .then(onOpen)
      .catch(() => setError('Could not open that claim.'))
  }

  if (rows === null) {
    return (
      <>
        <PageHeader title="My claims" />
        <p className="t-small text-muted">Loading…</p>
      </>
    )
  }
  if (rows.length === 0) {
    return (
      <>
        <PageHeader title="My claims" />
        <EmptyState
          title="No claims yet"
          body="Submit a prescription and its bills and they will appear here."
          action={
            <button
              type="button"
              onClick={onStart}
              className="rounded bg-seal px-5 py-2.5 text-sm font-semibold text-white hover:opacity-90"
            >
              Submit a claim
            </button>
          }
        />
      </>
    )
  }

  return (
    <>
      <PageHeader title="My claims" lede="What you have submitted, and where it has got to." />
      {error ? <p className="t-small mb-3 text-flag">{error}</p> : null}
      <div className="overflow-x-auto rounded bg-surface">
        <table className="w-full min-w-[34rem] border-collapse">
          <thead>
            <tr className="border-b border-ink-200 text-left">
              {['Submitted', 'Condition', 'Documents', 'Status'].map((head) => (
                <th key={head} className="t-colhead px-4 py-3">
                  {head}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const documents = [row.prescription_filename, row.bill_filename].filter(Boolean)
              return (
                <tr
                  key={row.id}
                  onClick={() => open(row.id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      open(row.id)
                    }
                  }}
                  tabIndex={0}
                  role="button"
                  aria-label={`Open the claim submitted on ${formatDate(row.created_at)}`}
                  className="cursor-pointer border-b border-ink-200 hover:bg-paper"
                >
                  <td className="t-small px-4 py-4 text-ink">
                    {formatDate(row.created_at)}
                    <span className="t-small ml-2 text-muted">{formatTime(row.created_at)}</span>
                  </td>
                  <td className="t-small px-4 py-4 text-ink">{row.condition ?? '—'}</td>
                  <td className="t-small px-4 py-4 text-muted">
                    {documents.length} file{documents.length === 1 ? '' : 's'}
                  </td>
                  <td className="t-small px-4 py-4 text-ink">
                    {REVIEW_LABEL[row.review_status] ?? row.review_status}
                    {row.certified_by_employee ? null : (
                      <span className="t-small ml-2 text-flag">Not certified</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </>
  )
}

export function History({
  session,
  onStart,
  onOpen,
}: {
  session: Session
  onStart: () => void
  onOpen: (detail: ScanDetail) => void
}) {
  // Reviewers only. `SubmissionHistory` above is what a submitter gets, chosen
  // in App so neither component runs the other's hooks.
  const admin = session.role === 'admin'
  const [scans, setScans] = useState<ScanSummary[] | null>(null)
  const [filters, setFilters] = useState<ScanFilters>(NO_FILTERS)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    listScans()
      .then(setScans)
      .catch((caught: unknown) => {
        setScans([])
        setError(caught instanceof Error ? caught.message : 'Could not load history.')
      })
  }

  useEffect(load, [])

  const employees = useMemo(() => {
    const seen = new Map<string, string>()
    for (const scan of scans ?? []) seen.set(scan.user_email, scan.employee_name)
    return [...seen.entries()]
  }, [scans])

  const visible = useMemo(() => applyFilters(scans ?? [], filters), [scans, filters])

  if (scans === null) {
    return (
      <>
        <PageHeader title="History" />
        <p className="t-small text-muted">Loading…</p>
      </>
    )
  }

  return (
    <>
      <PageHeader
        title="History"
        lede={
          admin
            ? 'Every reconciliation run on this instance, across all accounts.'
            : 'Reconciliations you have run.'
        }
      />

      {error ? <p className="t-small mb-4 text-flag">{error}</p> : null}

      {scans.length === 0 ? (
        <EmptyState
          title="Nothing here yet"
          body={
            admin
              ? 'Completed runs will be listed here with their verdict and the account that ran them, and any of them can be reopened.'
              : 'Your completed runs will be listed here, and any of them can be reopened.'
          }
          action={
            <button
              type="button"
              onClick={onStart}
              className="rounded bg-seal px-5 py-2.5 text-sm font-semibold text-white hover:opacity-90"
            >
              Run the first one
            </button>
          }
        />
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-end gap-3">
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

            {admin ? (
              <label className="block">
                <span className="t-micro block text-muted">Account</span>
                <select
                  value={filters.employee}
                  onChange={(e) => setFilters({ ...filters, employee: e.target.value })}
                  className="t-small mt-1 rounded bg-surface px-2.5 py-1.5 text-ink"
                >
                  <option value="all">Everyone</option>
                  {employees.map(([email, name]) => (
                    <option key={email} value={email}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}

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
              onClick={() => setFilters(NO_FILTERS)}
              className="t-small text-muted underline decoration-ink-300 underline-offset-4 hover:text-ink"
            >
              Clear
            </button>
            <span className="t-small ml-auto text-muted">
              {visible.length} of {scans.length}
            </span>
          </div>

          <div className="overflow-x-auto rounded bg-surface">
            <table className="w-full min-w-[46rem] border-collapse">
              <thead>
                <tr className="border-b border-ink-200 text-left">
                  {['Verdict', 'Date', 'Employee', 'Condition', 'Discrepancies', 'Supported', 'Time', ''].map((head) => (
                    <th key={head} className="t-micro px-4 py-3 text-muted">
                      {head}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visible.map((scan) => {
                  const open = () => {
                    getScan(scan.id)
                      .then(onOpen)
                      .catch(() => setError('Could not open that scan.'))
                  }
                  return (
                  // The whole row is the target. It was clickable before, but
                  // only on the verdict link and with no cursor or hover state,
                  // so nothing on screen said so.
                  <tr
                    key={scan.id}
                    onClick={open}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault()
                        open()
                      }
                    }}
                    tabIndex={0}
                    role="button"
                    aria-label={`Open the ${VERDICT_LABEL[scan.verdict]} scan from ${formatDate(scan.created_at)}`}
                    className="cursor-pointer border-b border-ink-100 last:border-b-0 hover:bg-paper focus-visible:bg-paper"
                  >
                    <td className="px-4 py-3">
                      <span className="flex items-center gap-2.5 text-left">
                        <span className="flex w-3.5 justify-center">
                          <SpineMark state={verdictState(scan.verdict)} />
                        </span>
                        <span className="t-small text-ink">{VERDICT_LABEL[scan.verdict]}</span>
                      </span>
                    </td>
                    <td className="t-small px-4 py-3 text-muted">
                      {formatDate(scan.created_at)}
                      <span className="t-small ml-2 text-ink-400">
                        {formatTime(scan.created_at)}
                      </span>
                    </td>
                    <td className="t-small px-4 py-3 text-ink">
                      {scan.employee_name}
                      <span className="t-small ml-2 text-muted">{scan.employee_number}</span>
                    </td>
                    <td className="t-small px-4 py-3 text-muted" title={scan.description ?? ''}>
                      {scan.condition ?? '—'}
                    </td>
                    <td className="t-data px-4 py-3 text-ink">
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
                    {/* What the prescription supports. Not an insurance
                        determination, and never labelled as one. */}
                    <td
                      className="t-data px-4 py-3 text-ink"
                      title="Billed lines with a prescription line behind them. Not an insurance determination."
                    >
                      {scan.currency ?? 'INR'}{' '}
                      {Number(scan.eligible_total ?? 0).toLocaleString('en-IN', {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2,
                      })}
                    </td>
                    <td className="t-data px-4 py-3 text-muted">
                      {(scan.processing_ms / 1000).toFixed(1)}s
                    </td>
                    <td className="px-4 py-3 text-right">
                      {admin ? (
                        <button
                          type="button"
                          onClick={(event) => {
                            // Never open the scan we are deleting.
                            event.stopPropagation()
                            deleteScan(scan.id)
                              .then(load)
                              .catch(() => setError('Could not delete that scan.'))
                          }}
                          className="t-small text-muted hover:text-flag"
                        >
                          Delete
                        </button>
                      ) : null}
                    </td>
                  </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {visible.length === 0 ? (
            <p className="t-small mt-4 text-muted">No scans match these filters.</p>
          ) : null}
        </>
      )}
    </>
  )
}
