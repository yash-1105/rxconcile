import { useEffect, useMemo, useState } from 'react'
import {
  fetchAllowance,
  getScan,
  getSubmission,
  listScans,
  listSubmissions,
} from '../api/client'
import type { Session } from '../auth/session'
import { BarList, TrendLine } from '../components/Charts'
import { EmptyState, PageHeader } from '../components/Shell'
import { SpineMark } from '../components/Spine'
import { listAllowances } from '../api/client'
import type { EmployeeScanDetail, EmployeeScanSummary } from '../types/api'

const REVIEW_LABEL: Record<string, string> = {
  submitted: 'Submitted',
  under_review: 'Under review',
  reviewed: 'Reviewed',
}

/** One money format for the whole app: "INR 12,000.00", never a bare number. */
function money(amount: string, currency = 'INR'): string {
  const value = Number(amount)
  return `${currency} ${
    Number.isFinite(value)
      ? value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      : amount
  }`
}
import type { AllowanceView } from '../types/api'
import {
  countBy,
  formatDate,
  perDay,
  totals,
  VERDICT_LABEL,
  verdictState,
} from '../lib/scans'
import type { ScanDetail, ScanSummary } from '../types/api'

function Stat({
  label,
  value,
  hint,
}: {
  label: string
  value: string
  hint: string
}) {
  return (
    <div className="rounded bg-surface px-5 py-4">
      <p className="t-micro text-muted">{label}</p>
      <p className="t-display mt-1 text-ink">{value}</p>
      <p className="t-small mt-1 text-muted">{hint}</p>
    </div>
  )
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded bg-surface px-5 py-4">
      <p className="t-micro text-muted">{title}</p>
      <div className="mt-3">{children}</div>
    </div>
  )
}

/**
 * A submitter's own overview.
 *
 * Their allowance, and how many claims are still with a reviewer. No
 * discrepancy counts and no checks-not-run: those are analysis, which is the
 * thing an employee does not do. The figures are not merely hidden here — the
 * server does not send them.
 */
export function EmployeeOverview({
  session,
  onStart,
  onOpen,
}: {
  session: Session
  onStart: () => void
  onOpen: (detail: EmployeeScanDetail) => void
}) {
  const [allowance, setAllowance] = useState<AllowanceView | null>(null)
  const [rows, setRows] = useState<EmployeeScanSummary[]>([])

  useEffect(() => {
    fetchAllowance(session.employeeNumber)
      .then(setAllowance)
      .catch(() => setAllowance(null))
    listSubmissions()
      .then(setRows)
      .catch(() => setRows([]))
  }, [session.employeeNumber])

  const firstName = session.name.split(' ')[0]
  const uncertified = rows.filter((row) => !row.certified_by_employee).length

  return (
    <>
      <PageHeader
        title={`Good to see you, ${firstName}`}
        lede="Your allowance, and the claims you have submitted."
      />

      {allowance === null ? (
        <p className="t-small text-muted">Your allowance could not be loaded.</p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-3">
          <Stat
            label="Balance remaining"
            value={money(allowance.balance)}
            hint={`Allowance year ${allowance.year}`}
          />
          <Stat label="Annual allowance" value={money(allowance.annual_amount)} hint="Per year" />
          {/* A COUNT, never an amount. A submitted claim's figure comes from
              defaults nobody has agreed to, and a balance that moved when a
              reviewer rejected a line would be worse than none. */}
          <Stat
            label="Awaiting review"
            value={String(allowance.awaiting_review)}
            hint="Not counted against your balance yet"
          />
        </div>
      )}

      {uncertified > 0 ? (
        <p className="t-small mt-4 text-flag">
          {uncertified} {uncertified === 1 ? 'claim is' : 'claims are'} not certified yet.
          Open {uncertified === 1 ? 'it' : 'them'} below to finish.
        </p>
      ) : null}

      <div className="mt-6">
        <Panel title="Your recent claims">
          {rows.length === 0 ? (
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
          ) : (
            <ul className="divide-y divide-ink-100">
              {rows.slice(0, 5).map((row) => (
                <li key={row.id}>
                  <button
                    type="button"
                    onClick={() => {
                      getSubmission(row.id).then(onOpen).catch(() => undefined)
                    }}
                    className="flex w-full items-baseline justify-between gap-3 py-2.5 text-left hover:bg-paper"
                  >
                    <span className="t-small text-ink">
                      {row.condition ?? 'Claim'}
                      <span className="t-small ml-2 text-muted">
                        {REVIEW_LABEL[row.review_status] ?? row.review_status}
                      </span>
                    </span>
                    <span className="t-small text-muted">{formatDate(row.created_at)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <p className="t-small mt-6 text-muted">
        Your balance counts reviewed claims only. Anything still with a reviewer is shown as a
        count above, because its amount has not been agreed yet.
      </p>
    </>
  )
}

export function Overview({
  session,
  onStart,
  onOpen,
}: {
  session: Session
  onStart: () => void
  onOpen: (detail: ScanDetail) => void
}) {
  // Reviewers only. `EmployeeOverview` above is what a submitter gets, chosen
  // in App so neither component runs the other's hooks.
  const admin = session.role === 'admin'
  const [scans, setScans] = useState<ScanSummary[] | null>(null)
  const [allowances, setAllowances] = useState<AllowanceView[]>([])

  useEffect(() => {
    listAllowances()
      .then(setAllowances)
      .catch(() => setAllowances([]))
  }, [])


  useEffect(() => {
    listScans()
      .then(setScans)
      .catch(() => setScans([]))
  }, [])

  const stats = useMemo(() => totals(scans ?? []), [scans])
  const recent = useMemo(() => (scans ?? []).slice(0, 5), [scans])

  // Rule-level detail is not in the summary columns, so the admin breakdown is
  // built from the stored results of the most recent scans rather than claimed
  // across all of history. The heading says so.
  const [ruleCounts, setRuleCounts] = useState<[string, number][]>([])
  useEffect(() => {
    if (!admin || !scans || scans.length === 0) return
    const sample = scans.slice(0, 20)
    Promise.all(sample.map((scan) => getScan(scan.id).catch(() => null)))
      .then((details) => {
        const codes = details
          .filter((detail): detail is ScanDetail => detail !== null)
          .flatMap((detail) =>
            detail.result.findings
              .filter((f) => f.severity === 'critical' || f.severity === 'warning')
              .map((f) => f.rule_code),
          )
        setRuleCounts(countBy(codes))
      })
      .catch(() => setRuleCounts([]))
  }, [admin, scans])

  const perEmployee = useMemo(() => {
    const counts = new Map<string, number>()
    for (const scan of scans ?? []) {
      counts.set(scan.employee_name, (counts.get(scan.employee_name) ?? 0) + 1)
    }
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([label, value]) => ({ label, value }))
  }, [scans])

  const firstName = session.name.split(' ')[0]

  if (scans === null) {
    return (
      <>
        <PageHeader title={`Good to see you, ${firstName}`} />
        <p className="t-small text-muted">Loading…</p>
      </>
    )
  }

  if (scans.length === 0) {
    return (
      <>
        <PageHeader
          title={`Good to see you, ${firstName}`}
          lede={
            admin
              ? 'Reconciliations across every account. Nothing has been recorded yet.'
              : 'Your reconciliations. Nothing has been recorded yet.'
          }
        />
        <EmptyState
          title="No reconciliations yet"
          body={
            admin
              ? 'Once anyone reconciles a prescription against a bill, every run will appear here with its verdict, who ran it, and what it found.'
              : 'Reconcile a prescription against a pharmacy bill and it will appear here with its verdict and what it found.'
          }
          action={
            <button
              type="button"
              onClick={onStart}
              className="rounded bg-seal px-5 py-2.5 text-sm font-semibold text-white hover:opacity-90"
            >
              Start a reconciliation
            </button>
          }
        />
      </>
    )
  }

  return (
    <>
      <PageHeader
        title={`Good to see you, ${firstName}`}
        lede={
          admin
            ? 'Reconciliations across every account.'
            : 'Your reconciliations.'
        }
      />

      <div className="grid gap-4 sm:grid-cols-4">
        <Stat
          label={admin ? 'Reconciliations' : 'Your reconciliations'}
          value={String(stats.scans)}
          hint={admin ? 'Across all accounts' : 'Run by you'}
        />
        <Stat
          label="Discrepancies found"
          value={String(stats.discrepancies)}
          hint={`${stats.criticals} serious`}
        />
        <Stat
          label="Checks not run"
          value={String(stats.checksUnavailable)}
          hint="Never counted as passes"
        />
        <Stat
          label="Median time"
          value={stats.medianMs === null ? '—' : `${(stats.medianMs / 1000).toFixed(1)}s`}
          hint="Per reconciliation"
        />
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <Panel title="Most recent">
          <ul className="divide-y divide-ink-100">
            {recent.map((scan) => (
              <li key={scan.id}>
                <button
                  type="button"
                  onClick={() => {
                    getScan(scan.id).then(onOpen).catch(() => undefined)
                  }}
                  className="flex w-full items-center gap-3 py-2.5 text-left hover:bg-ink-50"
                >
                  <span className="flex w-3.5 justify-center">
                    <SpineMark state={verdictState(scan.verdict)} />
                  </span>
                  <span className="t-small flex-1 text-ink">
                    {VERDICT_LABEL[scan.verdict]}
                    {scan.discrepancy_count > 0 ? (
                      <span className="t-small ml-2 text-muted">
                        {scan.discrepancy_count}
                      </span>
                    ) : null}
                  </span>
                  {admin ? (
                    <span className="t-small text-muted">{scan.employee_name}</span>
                  ) : null}
                  <span className="t-small text-muted">{formatDate(scan.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        </Panel>

        {admin ? (
          <>
            <Panel title="Allowance per employee">
              {allowances.length === 0 ? (
                <p className="t-small text-muted">No allowances to show yet.</p>
              ) : (
                <ul className="space-y-3">
                  {allowances.map((view) => {
                    const annual = Number(view.annual_amount)
                    const used = Number(view.used)
                    const share = annual > 0 ? Math.min(100, (used / annual) * 100) : 0
                    return (
                      <li key={view.employee_number}>
                        <div className="flex items-baseline justify-between gap-3">
                          <span className="t-small text-ink">
                            {view.employee_name || view.employee_number}
                            <span className="t-small ml-2 text-muted">{view.employee_number}</span>
                          </span>
                          <span className="t-data text-muted">
                            {money(view.balance)} of {money(view.annual_amount)} left
                          </span>
                        </div>
                        <div className="mt-1 h-1.5 w-full rounded bg-ink-100">
                          <div
                            className={`h-1.5 rounded ${share >= 100 ? 'bg-flag' : 'bg-seal'}`}
                            style={{ width: `${share}%` }}
                          />
                        </div>
                      </li>
                    )
                  })}
                </ul>
              )}
              <p className="t-small mt-3 text-muted">
                Allowance year {allowances[0]?.year ?? '\u2014'}. Used so far is the total of
                accepted lines on that employee&rsquo;s earlier claims in this window.
              </p>
            </Panel>

            <Panel title="Scans per employee">
              <BarList rows={perEmployee} />
            </Panel>
          </>
        ) : (
          <Panel title="Your verdicts">
            <BarList
              rows={countBy((scans ?? []).map((s) => VERDICT_LABEL[s.verdict])).map(
                ([label, value]) => ({ label, value }),
              )}
            />
          </Panel>
        )}
      </div>

      {admin ? (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <Panel title="Discrepancies by rule · last 20 scans">
            <BarList
              tone="problem"
              rows={ruleCounts.map(([label, value]) => ({ label, value }))}
              emptyLabel="No discrepancies in the scans sampled."
            />
          </Panel>
          <Panel title="Scans per day">
            <TrendLine points={perDay(scans)} />
          </Panel>
        </div>
      ) : null}

      <p className="t-small mt-6 text-muted">
        Every number here is counted from stored runs. Nothing is estimated, and there is no
        accuracy figure — what has been measured is whether a reading is reproducible, not
        whether it is correct.
      </p>
    </>
  )
}
