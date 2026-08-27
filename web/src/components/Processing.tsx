import { useEffect, useState } from 'react'

const STAGES = [
  { label: 'Reading prescription', detail: 'transcribing the handwriting' },
  { label: 'Reading bill', detail: 'parsing the invoice lines' },
  { label: 'Matching', detail: 'pairing lines and applying rules' },
] as const

/** Rough share of total wall time, used only to advance the indicator. */
const STAGE_SHARE = [0.45, 0.85, 1] as const
const ASSUMED_TOTAL_MS = 22_000

export function Processing({ runs }: { runs: number }) {
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    const started = Date.now()
    const timer = window.setInterval(() => setElapsed(Date.now() - started), 200)
    return () => window.clearInterval(timer)
  }, [])

  const expected = runs === 1 ? ASSUMED_TOTAL_MS * 0.9 : ASSUMED_TOTAL_MS
  const progress = Math.min(elapsed / expected, 0.98)
  const activeIndex = STAGE_SHARE.findIndex((share) => progress < share)
  const active = activeIndex === -1 ? STAGES.length - 1 : activeIndex

  return (
    <div className="mx-auto max-w-2xl py-16">
      <h2 className="text-center text-sm tracking-wide text-ink-500 uppercase">
        Reconciling
      </h2>
      <p className="mt-2 text-center text-sm text-ink-500">
        {runs * 2} extraction calls in flight · {runs === 1 ? 'one run' : 'three runs'} per
        document
      </p>

      <ol className="mt-10 space-y-4">
        {STAGES.map((stage, index) => {
          const done = index < active
          const current = index === active
          return (
            <li
              key={stage.label}
              className={`flex items-center gap-4 rounded border px-5 py-4 ${
                current
                  ? 'border-accent bg-white'
                  : done
                    ? 'border-ink-200 bg-white'
                    : 'border-ink-200 bg-ink-100/50'
              }`}
            >
              <span
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border font-mono text-xs ${
                  done
                    ? 'border-accent bg-accent text-white'
                    : current
                      ? 'border-accent text-accent'
                      : 'border-ink-300 text-ink-400'
                }`}
              >
                {done ? '✓' : index + 1}
              </span>
              <span className="flex-1">
                <span
                  className={`block text-sm font-semibold ${
                    done || current ? 'text-ink-900' : 'text-ink-400'
                  }`}
                >
                  {stage.label}
                </span>
                <span className="block text-sm text-ink-500">{stage.detail}</span>
              </span>
              {current ? (
                <span className="font-mono text-xs text-ink-500">working…</span>
              ) : null}
            </li>
          )
        })}
      </ol>

      <div className="mt-8 h-1 overflow-hidden rounded bg-ink-200">
        <div
          className="h-full bg-accent transition-[width] duration-200"
          style={{ width: `${progress * 100}%` }}
        />
      </div>
      <p className="mt-3 text-center font-mono text-xs text-ink-500">
        {(elapsed / 1000).toFixed(1)}s elapsed · typically around 22s
      </p>
    </div>
  )
}
