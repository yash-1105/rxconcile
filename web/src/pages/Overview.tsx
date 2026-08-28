import { useEffect, useMemo, useState } from 'react'
import { getScan, listScans } from '../api/client'
import type { Session } from '../auth/session'
import { BarList, TrendLine } from '../components/Charts'
import { EmptyState, PageHeader } from '../components/Shell'
import { SpineMark } from '../components/Spine'
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

export function Overview({
  session,
  onStart,
  onOpen,
}: {
  session: Session
  onStart: () => void
  onOpen: (detail: ScanDetail) => void
}) {
  const admin = session.role === 'admin'
  const [scans, setScans] = useState<ScanSummary[] | null>(null)

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
                      <span className="t-data ml-2 text-muted">
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
          <Panel title="Scans per employee">
            <BarList rows={perEmployee} />
          </Panel>
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
