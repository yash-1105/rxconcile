/**
 * Which billed items the prescription supports, and for how much.
 *
 * **Not an insurance determination, and the copy must never imply otherwise.**
 * Coverage rules, copay tiers and policy limits appear in neither document, so
 * none are modelled and none are inferred. The words "approved", "claim" and
 * "settlement" are deliberately absent: each would assert a decision this
 * system is in no position to make.
 *
 * Presentation only. Every amount and every line comes from the engine's own
 * assessment; nothing here adds, rounds or reclassifies anything.
 */

import { useState } from 'react'
import type { ReimbursementCategory, ReimbursementSummary } from '../types/api'
import { SpineMark, type SpineState } from './Spine'

const LABEL: Record<ReimbursementCategory, string> = {
  eligible: 'Supported by the prescription',
  not_eligible: 'Not supported by the prescription',
  needs_review: 'Needs review',
}

const BLURB: Record<ReimbursementCategory, string> = {
  eligible: 'Billed lines matched to a prescribed line with nothing against them.',
  not_eligible:
    'Billed lines the comparison found nothing on the prescription behind — unprescribed items, and prescription-only medicines with no order backing them.',
  needs_review:
    'Billed lines where a check could not be completed, or whose matched prescription line carries a discrepancy. Not a rejection: a statement that someone has to look.',
}

/** Grey for anything unresolved. Red only where the bill is unsupported. */
const MARK: Record<ReimbursementCategory, SpineState> = {
  eligible: 'clean',
  not_eligible: 'problem',
  needs_review: 'unchecked',
}

function money(currency: string, amount: string): string {
  const value = Number(amount)
  const formatted = Number.isFinite(value)
    ? value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : amount
  return `${currency} ${formatted}`
}

function Bucket({
  category,
  total,
  count,
  currency,
  summary,
}: {
  category: ReimbursementCategory
  total: string
  count: number
  currency: string
  summary: ReimbursementSummary
}) {
  const [open, setOpen] = useState(false)
  const lines = summary.lines.filter((line) => line.category === category)
  return (
    <div className="rounded border border-ink-200 bg-surface px-5 py-4">
      <div className="flex items-start gap-2.5">
        <SpineMark state={MARK[category]} className="mt-1.5" />
        <div className="min-w-0 flex-1">
          <h3 className="t-micro text-muted">{LABEL[category]}</h3>
          <p className="t-display mt-1 text-ink">{money(currency, total)}</p>
          <p className="t-small mt-1 text-muted">
            {count} {count === 1 ? 'line' : 'lines'}
          </p>
        </div>
      </div>
      <p className="t-small mt-3 text-muted">{BLURB[category]}</p>
      {lines.length > 0 ? (
        <>
          <button
            type="button"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            className="t-small mt-3 flex items-center gap-2 text-muted hover:text-ink"
          >
            <span className="t-data w-3 text-unknown">{open ? '−' : '+'}</span>
            {open ? 'Hide the lines' : 'Show the lines'}
          </button>
          {open ? (
            <ul className="mt-3 space-y-2 border-t border-ink-200 pt-3">
              {lines.map((line) => (
                <li key={line.item_id} className="flex items-baseline justify-between gap-4">
                  <span className="min-w-0">
                    <span className="t-data text-ink">{line.description}</span>
                    <span className="t-small mt-0.5 block text-muted">{line.reason}</span>
                  </span>
                  <span className="t-data shrink-0 text-ink">
                    {line.amount === null ? (
                      <span className="text-unknown" title="The bill prints no amount for this line">
                        not printed
                      </span>
                    ) : (
                      money(currency, line.amount)
                    )}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </>
      ) : null}
    </div>
  )
}

export function Reimbursement({ summary }: { summary: ReimbursementSummary | undefined }) {
  // Records stored before this existed carry no assessment. Saying so beats an
  // empty panel, which would read as "nothing was billed".
  if (!summary) {
    return (
      <p className="t-body text-muted">
        This result was recorded before the reimbursement assessment existed, so it carries
        none. Re-run the reconciliation to produce one.
      </p>
    )
  }
  if (summary.lines.length === 0) {
    return (
      <p className="t-body text-muted">
        This bill carries no lines to assess.
      </p>
    )
  }
  return (
    <div className="space-y-4">
      <p className="t-small max-w-3xl text-muted">
        An assessment of which billed items are supported by the prescription.{' '}
        <strong className="font-semibold text-ink">
          This is not an insurance determination.
        </strong>{' '}
        Coverage rules, copay tiers and policy limits appear in neither document, are not
        modelled here, and are not inferred.
      </p>
      <div className="grid gap-4 lg:grid-cols-3">
        <Bucket
          category="eligible"
          total={summary.eligible_total}
          count={summary.eligible_line_count}
          currency={summary.currency}
          summary={summary}
        />
        <Bucket
          category="not_eligible"
          total={summary.not_eligible_total}
          count={summary.not_eligible_line_count}
          currency={summary.currency}
          summary={summary}
        />
        <Bucket
          category="needs_review"
          total={summary.needs_review_total}
          count={summary.needs_review_line_count}
          currency={summary.currency}
          summary={summary}
        />
      </div>
      {summary.lines_without_amount > 0 ? (
        <p className="t-small text-muted">
          {summary.lines_without_amount} billed{' '}
          {summary.lines_without_amount === 1 ? 'line prints' : 'lines print'} no amount. They
          are excluded from these totals and are not counted as zero.
        </p>
      ) : null}
    </div>
  )
}
