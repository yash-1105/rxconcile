import type { Session } from '../auth/session'
import { EmptyState, PageHeader } from '../components/Shell'

export function History({ session, onStart }: { session: Session; onStart: () => void }) {
  const admin = session.role === 'admin'
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
      <EmptyState
        title="Nothing here yet"
        body={
          admin
            ? 'Completed runs will be listed here with their verdict, the account that ran them, and the two source documents — so any result can be reopened and checked against the originals.'
            : 'Your completed runs will be listed here with their verdict and the two source documents, so you can reopen any result and check it against the originals.'
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
      <p className="t-small mt-4 text-muted">
        Runs are not stored yet. This screen is wired and waiting for persistence.
      </p>
    </>
  )
}
