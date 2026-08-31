/**
 * How it works, for someone who will never open the code.
 *
 * The two claims that lead are the ones a client actually cares about: the
 * result is explainable, and the system says when it could not check something.
 * Nothing here mentions extraction runs, confidence scores or agreement.
 *
 * The limits section stays. A client who finds these out later trusts you less
 * than one who was told up front.
 */

import { Pipeline } from '../components/Pipeline'
import { PageHeader } from '../components/Shell'

const PILLARS: readonly { title: string; body: string }[] = [
  {
    title: 'Every finding can be explained',
    body: 'The comparison is done by fixed rules, not by judgement. Each finding names the rule behind it and points at the exact line on the original document, so nothing has to be taken on trust.',
  },
  {
    title: 'It tells you what it could not check',
    body: 'Where a document does not carry what a check needs — a missing price, an unreadable line — the result says the check did not run. A check that could not run is never reported as one that passed.',
  },
]

const LIMITS: readonly { title: string; body: string }[] = [
  {
    title: 'It compares documents; it does not give medical advice',
    body: 'No prescribing decision is assessed and no clinical judgement is made. Every finding needs a person.',
  },
  {
    title: 'It is not an insurance decision',
    body: 'Coverage rules, copay tiers and policy limits appear in neither document and are not applied. The reimbursement view says which billed items the prescription supports, and nothing more.',
  },
  {
    title: 'Quantities often cannot be confirmed',
    body: 'Pharmacy bills usually price per pack without saying whether the quantity column counts packs or tablets. Where the bill does not say, the line is marked for a manual check rather than passed.',
  },
  {
    title: 'The medicine and lab reference lists are illustrative',
    body: 'They were compiled for this demonstration rather than taken from a licensed drug master or a laboratory’s test list.',
  },
  {
    title: 'English and Latin script only',
    body: 'Lines in other scripts are left unread rather than filled in.',
  },
  {
    title: 'No accuracy figure has been established',
    body: 'This is a proof of concept. There is no benchmark behind it and no error rate to quote.',
  },
]

export function HowItWorks() {
  return (
    <>
      <PageHeader
        title="How it works"
        lede="Two documents in, a plain answer out — with the evidence for every line of it."
      />

      <div className="grid gap-4 lg:grid-cols-2">
        {PILLARS.map((pillar) => (
          <section key={pillar.title} className="rounded border border-ink-200 bg-surface px-5 py-4">
            <h2 className="t-title text-ink">{pillar.title}</h2>
            <p className="t-small mt-2 text-muted">{pillar.body}</p>
          </section>
        ))}
      </div>

      <h2 className="t-micro mt-10 mb-4">The five steps</h2>
      <Pipeline />

      <h2 className="t-micro mt-12 mb-1">What it does not do</h2>
      <p className="t-small mb-4 max-w-3xl text-muted">
        Stated up front rather than discovered later.
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        {LIMITS.map((limit) => (
          <div key={limit.title} className="rounded border border-ink-200 bg-surface px-5 py-4">
            <p className="t-small font-semibold text-ink">{limit.title}</p>
            <p className="t-small mt-1.5 text-muted">{limit.body}</p>
          </div>
        ))}
      </div>
    </>
  )
}
