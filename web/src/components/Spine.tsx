/**
 * The reconciliation spine — the one element this product is remembered by.
 *
 * A single vertical rule with the prescription on its left and the bill on its
 * right, like the fold of a ledger. Each row places one mark on it, and the
 * line BREAKS where the two documents disagree. A reader scans one vertical
 * edge and has the result before reading a word.
 *
 * State is carried by shape as well as colour, so it survives greyscale and
 * colour-blindness.
 */

export type SpineState = 'clean' | 'warning' | 'problem' | 'unchecked' | 'out-of-scope'

const MARK: Record<SpineState, { label: string; className: string }> = {
  clean: { label: 'Matches', className: 'bg-seal' },
  warning: { label: 'Check', className: 'bg-caution' },
  problem: { label: 'Problem', className: 'bg-flag' },
  unchecked: { label: 'Not checked', className: 'bg-unknown' },
  // Read, understood, and simply not a medicine. Neither a problem nor a gap.
  'out-of-scope': { label: 'Out of scope', className: 'bg-muted' },
}

/** A mark sitting on the spine, sized for a table row or a list item. */
export function SpineMark({ state, className = '' }: { state: SpineState; className?: string }) {
  const mark = MARK[state]
  if (state === 'problem') {
    // The break: two short stubs with a gap, so the line is visibly severed.
    return (
      <span
        role="img"
        aria-label={mark.label}
        title={mark.label}
        className={`inline-flex h-3.5 w-3.5 flex-col items-center justify-between ${className}`}
      >
        <span className="h-1 w-0.5 bg-flag" />
        <span className="h-1 w-0.5 bg-flag" />
      </span>
    )
  }
  if (state === 'warning') {
    return (
      <span
        role="img"
        aria-label={mark.label}
        title={mark.label}
        className={`inline-block h-2 w-2 rounded-full border-[1.5px] border-caution ${className}`}
      />
    )
  }
  if (state === 'out-of-scope') {
    // A hollow square: distinct in shape from every other mark, so it survives
    // greyscale like the rest.
    return (
      <span
        role="img"
        aria-label={mark.label}
        title={mark.label}
        className={`inline-block h-2 w-2 border-[1.5px] border-muted ${className}`}
      />
    )
  }
  if (state === 'unchecked') {
    return (
      <span
        role="img"
        aria-label={mark.label}
        title={mark.label}
        className={`inline-flex h-3.5 w-0.5 flex-col justify-between ${className}`}
      >
        <span className="h-[3px] w-full bg-unknown" />
        <span className="h-[3px] w-full bg-unknown" />
        <span className="h-[3px] w-full bg-unknown" />
      </span>
    )
  }
  return (
    <span
      role="img"
      aria-label={mark.label}
      title={mark.label}
      className={`inline-block h-1.5 w-1.5 rounded-full ${mark.className} ${className}`}
    />
  )
}

/** The vertical rule itself. Draws once on mount unless motion is reduced. */
export function SpineRule({ animate = false, className = '' }: { animate?: boolean; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={`block w-px bg-ink-200 ${animate ? 'anim-spine' : ''} ${className}`}
    />
  )
}
