/**
 * A walkthrough of the actual pipeline.
 *
 * Two claims lead, because they are the strongest arguments for the product
 * and both are structural rather than aspirational: the model never judges, and
 * a field the three readings disagree on is discarded rather than guessed.
 *
 * The limits section is not a disclaimer bolted on the end. A client who finds
 * these out later trusts you less than one who was told up front.
 */

import { PageHeader } from '../components/Shell'
import { SpineRule } from '../components/Spine'

const PILLARS: readonly { title: string; body: string }[] = [
  {
    title: 'The model extracts. It never judges.',
    body: 'The vision model transcribes each page into structured fields and stops there. It never sees the two documents together and never decides whether they agree. Every verdict comes from ordinary deterministic code, and every finding carries the name of the rule that produced it — which is what makes a result auditable rather than merely plausible.',
  },
  {
    title: 'Each document is read three times.',
    body: 'Every page is transcribed three times independently, and each field is resolved by how far the readings agree. A field the runs disagree on is discarded rather than settled by a majority of one. This replaced the model’s own confidence score, which was measured across 56 observations, never fell below 0.75, and was sometimes highest on the fields it could not reproduce — it carries no information, so nothing is allowed to depend on it.',
  },
]

const STAGES: readonly { title: string; body: string; note?: string }[] = [
  {
    title: 'Upload the two documents',
    body: 'A prescription and the bill it was dispensed against, as photographs or PDFs. Nothing is sent anywhere until both are present, and files above the size limit are rejected before anything is spent on them.',
  },
  {
    title: 'Prepare the image',
    body: 'Rotation is corrected from the photo’s own orientation data, the longest edge is reduced to 2000px and the page is re-encoded. Every later coordinate is measured against this version, so a highlight lands where the model actually looked.',
  },
  {
    title: 'Read the documents',
    body: 'A vision model transcribes each page into structured fields against a fixed schema. It reads only — it never sees both documents together and never decides whether they agree.',
    note: 'Anything illegible is returned empty rather than guessed. A blank field is a correct answer; a confident wrong drug name is the worst thing this system could produce.',
  },
  {
    title: 'Read it three times',
    body: 'Each document is transcribed three times independently and concurrently, so three readings cost roughly what one does. Each field is then resolved by agreement across those runs.',
    note: 'Three different readings of the same field resolve to nothing rather than to a majority of one, and the result records that the field was dropped rather than reporting it as read.',
  },
  {
    title: 'Normalise and match salts',
    body: 'Brand names resolve to their salt composition against the reference list, units are made comparable, and Indian dosing notation is parsed — 1-0-1, BD, TDS, x 5 days, 5/7. Every assumption here is a named constant, not a judgement call.',
    note: 'A brand and its generic resolve to the same salt, which is why a legal substitution is reported as a substitution rather than as a missing item.',
  },
  {
    title: 'Pair the lines',
    body: 'Prescribed lines are matched to billed lines by a composite of drug identity, strength and form, assigned so the overall pairing is the best available rather than the first one found. Ordered lab panels are decomposed into their analytes first, so one ordered LFT matches seven billed lines.',
  },
  {
    title: 'Apply the rules',
    body: 'Ordinary code, no model involved, compares the paired lines and raises a coded finding for each disagreement. Because the rules are deterministic, every verdict can be traced to the rule that produced it.',
    note: 'Where a rule cannot run because a value is missing, it says so. A check that could not run is never reported as a check that passed.',
  },
  {
    title: 'Reach a verdict',
    body: 'The findings decide the outcome: any serious finding is a mismatch, a document that could not be read reliably is inconclusive rather than either, and everything else is a match. The reimbursement view then sorts each billed line by whether the prescription supports it.',
    note: 'Inconclusive is a real answer, not a failure. It says the documents could not be compared — which is different from finding that they agree.',
  },
]

const LIMITS: readonly { title: string; body: string }[] = [
  {
    title: 'No accuracy claim has been established',
    body: 'What has been measured is whether a reading is reproducible across runs, not whether it is correct. There is no benchmark behind this, no ground-truth corpus, and no error rate to quote.',
  },
  {
    title: 'Quantity often cannot be checked at all',
    body: 'Indian pharmacy bills usually price per pack and rarely state whether the quantity column counts packs or individual tablets. Where the bill does not say, the quantity check does not run and the line is reported as needing a manual check — never as verified.',
  },
  {
    title: 'The medicine dictionary is illustrative',
    body: 'Around 280 brands, hand-compiled to exercise brand-to-salt resolution and not verified against any regulatory source. Strengths are indicative and schedule classifications approximate. A production system needs a maintained, licensed drug master.',
  },
  {
    title: 'The lab panel table is hand-compiled too',
    body: 'Panel compositions vary between laboratories — one lab’s LFT is not another’s — and this table was written for this build rather than taken from any laboratory’s test master. Decomposition is evidenced by one real handwriting sample and a synthetic bill, not by a corpus.',
  },
  {
    title: 'English and Latin script only',
    body: 'Where a page carried Bengali, the transcription was found to be generated rather than read. Those lines are left unread rather than filled in.',
  },
  {
    title: 'It makes no clinical judgement',
    body: 'It gives no medical advice, assesses no prescribing decision, and determines no insurance outcome. It compares two documents and reports where they differ. Every finding needs a human.',
  },
]

export function HowItWorks() {
  return (
    <>
      <PageHeader
        title="How it works"
        lede="The model turns pixels into data; ordinary code decides whether the two documents agree. That separation is the whole design."
      />

      <div className="grid gap-4 lg:grid-cols-2">
        {PILLARS.map((pillar) => (
          <section key={pillar.title} className="rounded border border-ink-200 bg-surface px-5 py-4">
            <h2 className="t-title text-ink">{pillar.title}</h2>
            <p className="t-small mt-2 text-muted">{pillar.body}</p>
          </section>
        ))}
      </div>

      <h2 className="t-micro mt-10 mb-4 text-muted">The pipeline, in order</h2>
      <ol className="space-y-0">
        {STAGES.map((stage, index) => (
          <li key={stage.title} className="flex gap-5">
            <div className="flex w-8 shrink-0 flex-col items-center">
              <span className="t-data flex h-8 w-8 items-center justify-center rounded-full bg-surface text-muted">
                {index + 1}
              </span>
              {index < STAGES.length - 1 ? <SpineRule className="flex-1" /> : null}
            </div>
            <div className={index < STAGES.length - 1 ? 'pb-8' : ''}>
              <h3 className="t-title text-ink">{stage.title}</h3>
              <p className="t-body mt-1.5 max-w-2xl text-muted">{stage.body}</p>
              {stage.note ? (
                <p className="t-small mt-2 max-w-2xl rounded bg-surface px-4 py-3 text-muted">
                  {stage.note}
                </p>
              ) : null}
            </div>
          </li>
        ))}
      </ol>

      <h2 className="t-micro mt-12 mb-1 text-muted">What it does not do</h2>
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
