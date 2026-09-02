/**
 * The pipeline as a diagram: upload, read, match, check, report.
 *
 * Written for someone who will never open the code. No extraction runs, no
 * confidence scores, no agreement ratios — the five things that happen, in
 * order. The sentence that used to sit under each stage was removed in a copy
 * pass; the diagram is the numbered sequence and nothing more.
 *
 * Laid out as a row on a wide screen and a column on a narrow one, with the
 * connector rotating to match. Every stage keeps its number, so the sequence
 * survives even when the arrows do not.
 */

const STAGES: readonly { title: string }[] = [
  { title: 'Upload' },
  { title: 'Read' },
  { title: 'Match' },
  { title: 'Check' },
  { title: 'Report' },
]

function Arrow({ className = '' }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 8"
      aria-hidden="true"
      className={`h-2 w-6 shrink-0 text-ink-300 ${className}`}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
    >
      <path d="M0 4h20" />
      <path d="M17 1.5 20.5 4 17 6.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function Pipeline() {
  return (
    <ol className="flex flex-col gap-3 lg:flex-row lg:items-stretch lg:gap-0">
      {STAGES.map((stage, index) => (
        <li key={stage.title} className="flex items-stretch gap-3 lg:flex-1 lg:gap-0">
          <div className="flex-1 rounded border border-ink-200 bg-surface px-5 py-4">
            <span className="t-micro text-muted">Step {index + 1}</span>
            <h3 className="t-title mt-1 text-ink">{stage.title}</h3>
          </div>
          {index < STAGES.length - 1 ? (
            <span className="flex items-center justify-center px-2" aria-hidden="true">
              {/* Points down when the stages stack, right when they sit in a row. */}
              <Arrow className="rotate-90 lg:rotate-0" />
            </span>
          ) : null}
        </li>
      ))}
    </ol>
  )
}
