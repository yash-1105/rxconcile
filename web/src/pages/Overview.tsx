import type { Session } from '../auth/session'
import { EmptyState, PageHeader } from '../components/Shell'
import { SpineMark } from '../components/Spine'

/** A statistic with nothing behind it yet says so, rather than showing zero. */
function Stat({ label, hint }: { label: string; hint: string }) {
  return (
    <div className="rounded bg-surface px-5 py-4">
      <p className="t-micro text-muted">{label}</p>
      <p className="t-display mt-1 text-ink-300">—</p>
      <p className="t-small mt-1 text-muted">{hint}</p>
    </div>
  )
}

export function Overview({
  session,
  onStart,
}: {
  session: Session
  onStart: () => void
}) {
  const admin = session.role === 'admin'
  return (
    <>
      <PageHeader
        title={`Good to see you, ${session.name.split(' ')[0]}`}
        lede={
          admin
            ? 'Reconciliations across every account. Nothing has been recorded yet.'
            : 'Your reconciliations. Nothing has been recorded yet.'
        }
      />

      <div className="grid gap-4 sm:grid-cols-3">
        <Stat
          label={admin ? 'Reconciliations, all users' : 'Your reconciliations'}
          hint="Counts appear once you run one"
        />
        <Stat
          label="Discrepancies found"
          hint="Serious findings across those runs"
        />
        <Stat
          label="Checks that could not run"
          hint="Tracked separately — never counted as passes"
        />
      </div>

      <div className="mt-8">
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
      </div>

      <div className="mt-8 rounded bg-surface px-5 py-4">
        <p className="t-micro text-muted">What the marks mean</p>
        <ul className="mt-3 grid gap-3 sm:grid-cols-4">
          {(
            [
              ['clean', 'Matches', 'Nothing to act on'],
              ['warning', 'Check', 'Differs, worth a look'],
              ['problem', 'Problem', 'The bill does not match'],
              ['unchecked', 'Not checked', 'Could not be verified'],
            ] as const
          ).map(([state, label, hint]) => (
            <li key={label} className="flex items-start gap-2.5">
              <span className="mt-1 flex w-3.5 justify-center">
                <SpineMark state={state} />
              </span>
              <span>
                <span className="t-small block font-medium text-ink">{label}</span>
                <span className="t-small block text-muted">{hint}</span>
              </span>
            </li>
          ))}
        </ul>
      </div>
    </>
  )
}
