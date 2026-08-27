import { useCallback, useState } from 'react'
import type {
  DocSide,
  Finding,
  ItemCountUnstableDetail,
  ReconciliationResult,
  Verdict,
} from '../types/api'
import { AuditPanel } from './Audit'
import { ComparisonTable } from './ComparisonTable'
import { FindingsList, type LocateResult } from './Findings'
import { MetaStat, Panel } from './primitives'

const VERDICT_STYLE: Record<Verdict, { band: string; label: string; copy: string }> = {
  match: {
    band: 'border-emerald-300 bg-emerald-50',
    label: 'Match',
    copy: 'Every prescribed item was matched to a billed line with no discrepancies found.',
  },
  match_with_warnings: {
    band: 'border-amber-300 bg-amber-50',
    label: 'Match with warnings',
    copy: 'The documents correspond, but some lines differ in ways worth checking.',
  },
  mismatch: {
    band: 'border-red-300 bg-red-50',
    label: 'Mismatch',
    copy: 'At least one critical discrepancy was found between the prescription and the bill.',
  },
  inconclusive: {
    band: 'border-slate-300 bg-slate-100',
    label: 'Inconclusive',
    copy:
      'These documents could not be read reliably enough to compare. This is not a finding ' +
      'that they match, and it is not a finding that they differ — the extraction was not ' +
      'consistent enough to support either conclusion. Review the pages by hand.',
  },
}

function VerdictBanner({ result }: { result: ReconciliationResult }) {
  const style = VERDICT_STYLE[result.verdict]
  const inconclusive = result.verdict === 'inconclusive'
  return (
    <section className={`rounded border px-6 py-5 ${style.band}`}>
      <div className="flex flex-wrap items-start justify-between gap-6">
        <div className="max-w-3xl">
          <p className="text-xs tracking-wide text-ink-600 uppercase">Verdict</p>
          <h2 className="mt-1 text-2xl font-semibold text-ink-900">{style.label}</h2>
          <p className="mt-2 text-sm text-ink-700">{style.copy}</p>
        </div>
        <dl className="flex gap-8">
          <MetaStat
            label="Score"
            value={
              result.score === null ? (
                <span className="text-ink-500" title="Not measurable">
                  — <span className="text-sm">not scored</span>
                </span>
              ) : (
                result.score.toFixed(0)
              )
            }
          />
          <MetaStat label="Time" value={`${(result.processing_ms / 1000).toFixed(1)}s`} />
          <MetaStat label="Findings" value={result.findings.length} />
        </dl>
      </div>
      {inconclusive ? (
        <p className="mt-4 border-t border-slate-300 pt-3 font-mono text-xs text-slate-700">
          score is null because nothing was reliably measured — it is not zero
        </p>
      ) : null}
    </section>
  )
}

function ReviewSummaryPanel({ result }: { result: ReconciliationResult }) {
  const summary = result.review_summary
  const measured = summary.agreement_measured
  const unavailable = summary.checks_unavailable
  const cells: { label: string; value: number | null; hint: string }[] = [
    {
      label: 'Items needing review',
      value: summary.items_needing_review,
      hint: 'Items with at least one field the runs did not fully agree on',
    },
    {
      label: 'Fields nulled by disagreement',
      value: summary.fields_nulled_by_disagreement,
      hint: 'Fields left null because the runs produced three different readings',
    },
    {
      label: 'Unstable lines',
      value: summary.unstable_line_count,
      hint: 'Lines present in some extraction runs but not all',
    },
  ]
  return (
    <Panel title="Review summary">
      <dl className="grid gap-6 sm:grid-cols-4">
        <div>
          <dt className="text-xs tracking-wide text-ink-500 uppercase">Checks not run</dt>
          <dd
            className={`mt-1 font-mono text-2xl ${
              unavailable > 0 ? 'text-amber-700' : 'text-ink-900'
            }`}
          >
            {unavailable}
          </dd>
          <p className="mt-1 text-xs text-ink-500">
            Rules that could not run because a value was absent from the documents
          </p>
        </div>
        {cells.map(({ label, value, hint }) => (
          <div key={label}>
            <dt className="text-xs tracking-wide text-ink-500 uppercase">{label}</dt>
            <dd className="mt-1 font-mono text-2xl text-ink-900">
              {measured && value !== null ? (
                value
              ) : (
                <span className="text-base text-ink-400">not measured</span>
              )}
            </dd>
            <p className="mt-1 text-xs text-ink-500">{hint}</p>
          </div>
        ))}
      </dl>
      {unavailable > 0 ? (
        <p className="mt-4 border-t border-ink-200 pt-3 text-sm text-amber-800">
          <span className="font-semibold">
            {unavailable} check{unavailable > 1 ? 's' : ''} could not run.
          </span>{' '}
          A verdict of &ldquo;match&rdquo; means no discrepancy was found among the checks
          that <em>did</em> run. It does not mean these ones passed — they were never
          performed. They are listed under Findings as{' '}
          <span className="font-mono text-xs">CHECK_UNAVAILABLE</span>.
        </p>
      ) : null}
      {!measured ? (
        <p className="mt-4 border-t border-ink-200 pt-3 text-sm text-amber-800">
          This run used a single extraction pass, so agreement could not be measured. These are
          reported as not measured rather than as zero — a single run cannot establish that
          nothing needs review.
        </p>
      ) : null}
    </Panel>
  )
}

/**
 * A line seen in some runs and not others is the most dangerous state this
 * system reports: the engine has nothing to match, so it raises no discrepancy
 * and the line reads as a clean match. It gets its own panel, near the verdict.
 */
function InstabilityPanel({ result }: { result: ReconciliationResult }) {
  const findings = result.findings.filter((f) => f.rule_code === 'ITEM_COUNT_UNSTABLE')
  if (findings.length === 0) return null
  return (
    <section className="rounded border-2 border-red-400 bg-red-50 px-6 py-5">
      <p className="text-xs tracking-wide text-red-800 uppercase">
        Document instability · critical
      </p>
      <h3 className="mt-1 text-lg font-semibold text-red-900">
        Some lines were not seen by every extraction run
      </h3>
      <p className="mt-2 max-w-3xl text-sm text-red-800">
        A line that appears in some runs and not others cannot be reconciled: the engine has
        nothing to match it against, so it raises no discrepancy and the line reads as agreement.
        Treat the comparison below as incomplete and check these lines on the source image.
      </p>
      <div className="mt-4 space-y-3">
        {findings.map((finding, index) => {
          const detail = finding.detail as unknown as ItemCountUnstableDetail
          return (
            <div key={index} className="rounded border border-red-300 bg-white px-4 py-3">
              <p className="font-mono text-xs text-red-800">
                {detail.document} · item counts per run [{detail.run_item_counts.join(', ')}]
              </p>
              {detail.unstable_lines.length > 0 ? (
                <ul className="mt-2 space-y-1">
                  {detail.unstable_lines.map((line, lineIndex) => (
                    <li
                      key={lineIndex}
                      className="rounded bg-red-50 px-2 py-1 font-mono text-xs break-words text-red-900"
                    >
                      {line}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          )
        })}
      </div>
    </section>
  )
}

export function Result({
  result,
  prescriptionImage,
  billImage,
  onReset,
}: {
  result: ReconciliationResult
  prescriptionImage: string | null
  billImage: string | null
  onReset: () => void
}) {
  const [showAudit, setShowAudit] = useState(false)
  const [highlight, setHighlight] = useState<{ side: DocSide; itemId: string } | null>(null)

  const itemHasBox = useCallback(
    (side: DocSide, itemId: string): boolean => {
      const items = side === 'prescription' ? result.prescription.items : result.bill.items
      return items.find((item) => item.item_id === itemId)?.bbox != null
    },
    [result],
  )

  /** Point the image at a finding's line, and report whether that was possible. */
  const locate = useCallback(
    (finding: Finding): LocateResult => {
      const target: { side: DocSide; itemId: string } | null = finding.prescribed_ref
        ? { side: 'prescription', itemId: finding.prescribed_ref }
        : finding.billed_ref
          ? { side: 'bill', itemId: finding.billed_ref }
          : null
      if (!target) {
        setHighlight(null)
        return 'no-ref'
      }
      if (!itemHasBox(target.side, target.itemId)) {
        setHighlight(null)
        return 'not-located'
      }
      setShowAudit(true)
      setHighlight(target)
      return 'located'
    },
    [itemHasBox],
  )

  const hoverRow = useCallback(
    (row: { prescribedId: string | null; billedId: string | null } | null) => {
      if (!showAudit) return
      if (!row) {
        setHighlight(null)
        return
      }
      if (row.prescribedId && itemHasBox('prescription', row.prescribedId)) {
        setHighlight({ side: 'prescription', itemId: row.prescribedId })
      } else if (row.billedId && itemHasBox('bill', row.billedId)) {
        setHighlight({ side: 'bill', itemId: row.billedId })
      } else {
        setHighlight(null)
      }
    },
    [itemHasBox, showAudit],
  )

  return (
    <div className="space-y-6">
      <VerdictBanner result={result} />
      <ReviewSummaryPanel result={result} />
      <InstabilityPanel result={result} />

      <Panel
        title="Comparison"
        subtitle={`${result.matched_pairs.length} paired · ${result.unmatched_prescribed.length} prescribed unmatched · ${result.unmatched_billed.length} billed unmatched`}
      >
        <ComparisonTable result={result} onHover={hoverRow} />
      </Panel>

      <Panel title="Findings">
        <FindingsList
          findings={result.findings}
          verdict={result.verdict}
          onLocate={locate}
        />
      </Panel>

      <div>
        <button
          type="button"
          onClick={() => setShowAudit(!showAudit)}
          className="rounded border border-ink-300 bg-white px-4 py-2 text-sm text-ink-700 hover:bg-ink-100"
        >
          {showAudit ? 'Hide audit' : 'Show audit'}
        </button>
        <span className="ml-3 text-sm text-ink-500">
          Click a finding to highlight its line on the image; hover a comparison row to
          do the same.
        </span>
      </div>
      {showAudit ? (
        <AuditPanel
          prescription={result.prescription}
          bill={result.bill}
          prescriptionImage={prescriptionImage}
          billImage={billImage}
          highlight={highlight}
        />
      ) : null}

      <div className="pt-2">
        <button
          type="button"
          onClick={onReset}
          className="rounded bg-accent px-5 py-2.5 text-sm font-semibold text-white hover:opacity-90"
        >
          Reconcile another
        </button>
      </div>
    </div>
  )
}
