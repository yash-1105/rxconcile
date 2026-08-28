import { EmptyState, PageHeader } from '../components/Shell'

export function Dictionary() {
  return (
    <>
      <PageHeader
        title="Medicine dictionary"
        lede="The reference list used to resolve a brand on a bill to the medicine it actually is, so a generic substitution is recognised rather than reported as a missing item."
      />

      <EmptyState
        title="Not connected yet"
        body="This will list every brand the matcher knows, with its salt composition, indicative strengths, therapeutic class and schedule — searchable by brand or by salt."
      />

      <div className="mt-6 grid gap-4 sm:grid-cols-3">
        {(
          [
            ['Brand → salt', 'Dolo and Calpol both resolve to paracetamol, so either satisfies the other.'],
            ['Schedule', 'Marks prescription-only medicines, which powers the unbacked-dispensing check.'],
            ['Therapeutic class', 'Catches a fuzzy match that landed on a different kind of medicine.'],
          ] as const
        ).map(([title, body]) => (
          <div key={title} className="rounded bg-surface px-5 py-4">
            <p className="t-small font-medium text-ink">{title}</p>
            <p className="t-small mt-1 text-muted">{body}</p>
          </div>
        ))}
      </div>

      <p className="t-small mt-6 text-muted">
        The list is illustrative proof-of-concept data, hand-compiled and not verified against
        any regulatory source. It is not suitable for clinical or dispensing decisions.
      </p>
    </>
  )
}
