import { PageHeader } from '../components/Shell'
import { SpineRule } from '../components/Spine'

const STAGES: readonly { title: string; body: string; note?: string }[] = [
  {
    title: 'Prepare the image',
    body: 'Rotation is corrected from the photo’s own orientation data, the longest edge is reduced to 2000px and the page is re-encoded. Files above the size limit are rejected before anything is spent on them.',
  },
  {
    title: 'Read the documents',
    body: 'A vision model transcribes each page into structured fields against a fixed schema. It reads only — it never sees both documents together and never decides whether they agree.',
    note: 'Anything illegible is returned empty rather than guessed. A blank field is a correct answer; a confident wrong drug name is the worst thing this system could produce.',
  },
  {
    title: 'Read it three times',
    body: 'Each document is transcribed three times independently, and each field is resolved by how far the readings agree. Three different readings of the same field resolve to nothing rather than to a majority of one.',
    note: 'The model’s own confidence score is deliberately ignored: measured across 56 observations it never fell below 0.75, and was sometimes highest on fields it could not reproduce.',
  },
  {
    title: 'Normalise',
    body: 'Brand names resolve to their salt composition, units are made comparable, and Indian dosing notation is parsed — 1-0-1, BD, TDS, x 5 days, 5/7. Every assumption here is a named constant, not a judgement call.',
  },
  {
    title: 'Pair the lines',
    body: 'Prescribed lines are matched to billed lines by a composite of drug identity, strength and form, assigned so the overall pairing is the best available rather than the first one found.',
    note: 'A brand and its generic pair as the same medicine, which is why a legal substitution is reported as a substitution rather than as a missing item.',
  },
  {
    title: 'Apply the rules',
    body: 'Ordinary code, no model involved, compares the paired lines and raises a coded finding for each disagreement. Because the rules are deterministic, every verdict can be traced to the rule that produced it.',
    note: 'Where a rule cannot run because a value is missing, it says so. A check that could not run is never reported as a check that passed.',
  },
]

export function HowItWorks() {
  return (
    <>
      <PageHeader
        title="How it works"
        lede="Six stages. The model turns pixels into data; ordinary code decides whether the two documents agree. That separation is the whole design."
      />

      <div className="relative">
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
                <h2 className="t-title text-ink">{stage.title}</h2>
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
      </div>

      <div className="mt-10 rounded bg-surface px-5 py-4">
        <p className="t-micro text-muted">What it does not do</p>
        <ul className="t-small mt-3 space-y-1.5 text-muted">
          <li>It gives no medical advice and makes no clinical judgement. It compares documents.</li>
          <li>
            It makes no accuracy claim. What has been measured is whether the reading is
            reproducible, not whether it is correct.
          </li>
          <li>It is validated on English and Latin script only.</li>
        </ul>
      </div>
    </>
  )
}
