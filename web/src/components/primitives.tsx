import type { ReactNode } from 'react'
import type { Severity } from '../types/api'

export function Panel({
  title,
  subtitle,
  children,
  className = '',
}: {
  title?: string
  subtitle?: string
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`rounded border border-ink-200 bg-white ${className}`}>
      {title ? (
        <header className="border-b border-ink-200 px-5 py-3">
          <h2 className="text-sm font-semibold tracking-wide text-ink-700 uppercase">{title}</h2>
          {subtitle ? <p className="mt-1 text-sm text-ink-500">{subtitle}</p> : null}
        </header>
      ) : null}
      <div className="px-5 py-4">{children}</div>
    </section>
  )
}

const SEVERITY_CHIP: Record<Severity, string> = {
  critical: 'border-red-300 bg-red-50 text-red-800',
  warning: 'border-amber-300 bg-amber-50 text-amber-900',
  info: 'border-ink-300 bg-ink-100 text-ink-600',
}

export function RuleChip({ code, severity }: { code: string; severity: Severity }) {
  return (
    <span
      className={`inline-block rounded border px-2 py-0.5 font-mono text-xs ${SEVERITY_CHIP[severity]}`}
    >
      {code}
    </span>
  )
}

/** A value that may legitimately be absent. Null renders as an em-dash, never 0. */
export function Value({ children }: { children: ReactNode }) {
  const empty = children === null || children === undefined || children === ''
  return empty ? (
    <span className="font-mono text-ink-400" title="Not present">
      —
    </span>
  ) : (
    <span className="font-mono text-ink-900">{children}</span>
  )
}

/**
 * Per-field agreement across extraction runs. This replaces model confidence
 * everywhere: the model's own score was measured to carry no information.
 */
export function AgreementBadge({ ratio }: { ratio: number | null }) {
  if (ratio === null) {
    return (
      <span
        className="font-mono text-xs text-ink-400"
        title="Single-run extraction: agreement was not measured"
      >
        n/a
      </span>
    )
  }
  const tone =
    ratio >= 1 ? 'text-ink-500' : ratio >= 0.6 ? 'text-amber-700' : 'text-red-700 font-semibold'
  return (
    <span
      className={`font-mono text-xs ${tone}`}
      title={`${Math.round(ratio * 100)}% of extraction runs agreed on this field`}
    >
      {ratio.toFixed(2)}
    </span>
  )
}

export function MetaStat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <dt className="text-xs tracking-wide text-ink-500 uppercase">{label}</dt>
      <dd className="mt-1 font-mono text-lg text-ink-900">{value}</dd>
    </div>
  )
}
