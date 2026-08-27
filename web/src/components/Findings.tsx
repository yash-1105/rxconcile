import { useState } from 'react'
import type {
  Finding,
  QuantityAmbiguousDetail,
  Severity,
  Verdict,
} from '../types/api'
import { RuleChip } from './primitives'

function DetailTable({ detail }: { detail: Record<string, unknown> }) {
  const entries = Object.entries(detail)
  if (entries.length === 0) return null
  return (
    <dl className="mt-3 grid gap-x-6 gap-y-1 border-t border-ink-200 pt-3 sm:grid-cols-[auto_1fr]">
      {entries.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="font-mono text-xs text-ink-500">{key}</dt>
          <dd className="font-mono text-xs break-all text-ink-700">
            {typeof value === 'object' && value !== null
              ? JSON.stringify(value)
              : String(value)}
          </dd>
        </div>
      ))}
    </dl>
  )
}

/**
 * QUANTITY_AMBIGUOUS is not a discrepancy. The bill does not say whether its
 * quantity column counts packs or units, so the check could not be performed at
 * all. Rendering it as a warning would assert something the data cannot support.
 */
function QuantityAmbiguousCard({ finding }: { finding: Finding }) {
  const detail = finding.detail as unknown as QuantityAmbiguousDetail
  const { as_units: asUnits, as_packs: asPacks } = detail.interpretations
  return (
    <div className="rounded border border-ink-300 bg-white p-4">
      <div className="flex flex-wrap items-center gap-3">
        <RuleChip code={finding.rule_code} severity="info" />
        <span className="text-sm font-semibold text-ink-700">
          Quantity could not be checked on this bill
        </span>
      </div>
      <p className="mt-2 max-w-3xl text-sm text-ink-600">
        The bill does not state whether its quantity column counts whole packs or individual
        units, and the two readings lead to different conclusions. No quantity discrepancy is
        asserted either way.
      </p>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        {[
          { label: 'Read as units', reading: asUnits },
          { label: 'Read as packs', reading: asPacks },
        ].map(({ label, reading }) => (
          <div key={label} className="rounded border border-ink-200 bg-ink-50 px-4 py-3">
            <p className="text-xs tracking-wide text-ink-500 uppercase">{label}</p>
            <p className="mt-1 font-mono text-lg text-ink-900">{reading.billed_units}</p>
            <p className="mt-1 font-mono text-xs text-ink-500">
              {reading.outcome ?? 'no discrepancy'}
            </p>
          </div>
        ))}
      </div>
      <dl className="mt-4 flex flex-wrap gap-x-8 gap-y-2 border-t border-ink-200 pt-3">
        <div>
          <dt className="text-xs text-ink-500">Expected</dt>
          <dd className="font-mono text-sm text-ink-900">{detail.expected_units}</dd>
        </div>
        <div>
          <dt className="text-xs text-ink-500">Pack</dt>
          <dd className="font-mono text-sm text-ink-900">{detail.pack_size ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-xs text-ink-500">Basis resolved by</dt>
          <dd className="font-mono text-sm text-ink-900">{detail.basis_method}</dd>
        </div>
      </dl>
    </div>
  )
}

export type LocateResult = 'located' | 'not-located' | 'no-ref'

function FindingRow({
  finding,
  onLocate,
}: {
  finding: Finding
  onLocate: (finding: Finding) => LocateResult
}) {
  const [open, setOpen] = useState(false)
  const [located, setLocated] = useState<LocateResult | null>(null)
  const refs = [finding.prescribed_ref, finding.billed_ref].filter(Boolean) as string[]
  return (
    <div className="rounded border border-ink-200 bg-white">
      <button
        type="button"
        onClick={() => {
          setOpen(!open)
          setLocated(onLocate(finding))
        }}
        className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-ink-50"
      >
        <RuleChip code={finding.rule_code} severity={finding.severity} />
        <span className="flex-1 text-sm text-ink-800">{finding.message}</span>
        {refs.length > 0 ? (
          <span className="shrink-0 font-mono text-xs text-ink-400">{refs.join(' · ')}</span>
        ) : (
          <span className="shrink-0 font-mono text-xs text-ink-400">document</span>
        )}
        <span className="shrink-0 font-mono text-xs text-ink-400">{open ? '−' : '+'}</span>
      </button>
      {open ? (
        <div className="px-4 pb-4">
          {/* Honesty about provenance: a finding that cannot be pointed at must
              say so rather than quietly failing to highlight anything. */}
          {located === 'not-located' ? (
            <p className="mt-2 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              This line could not be located on the source image, so there is nothing to
              highlight. Read it off the page yourself before acting on this finding.
            </p>
          ) : null}
          {located === 'no-ref' ? (
            <p className="mt-2 rounded border border-ink-200 bg-ink-50 px-3 py-2 text-xs text-ink-600">
              This is a document-level finding. It does not refer to a single line, so
              there is no region to highlight.
            </p>
          ) : null}
          <DetailTable detail={finding.detail} />
        </div>
      ) : null}
    </div>
  )
}

const GROUPS: { severity: Severity; heading: string }[] = [
  { severity: 'critical', heading: 'Critical' },
  { severity: 'warning', heading: 'Warnings' },
]

export function FindingsList({
  findings,
  verdict,
  onLocate,
}: {
  findings: Finding[]
  verdict: Verdict
  onLocate: (finding: Finding) => LocateResult
}) {
  const [showInfo, setShowInfo] = useState(false)
  const ambiguous = findings.filter((f) => f.rule_code === 'QUANTITY_AMBIGUOUS')
  const unavailable = findings.filter((f) => f.rule_code === 'CHECK_UNAVAILABLE')
  // ITEM_COUNT_UNSTABLE gets its own prominent panel; do not repeat it here.
  const rest = findings.filter(
    (f) =>
      f.rule_code !== 'QUANTITY_AMBIGUOUS' &&
      f.rule_code !== 'ITEM_COUNT_UNSTABLE' &&
      f.rule_code !== 'CHECK_UNAVAILABLE',
  )
  const info = rest.filter((f) => f.severity === 'info')
  const provisional = verdict === 'inconclusive'

  return (
    <div className="space-y-6">
      {provisional ? (
        <div className="rounded border border-slate-300 bg-slate-50 px-5 py-4">
          <p className="text-sm font-semibold text-slate-800">
            The findings below are provisional observations, not assertions.
          </p>
          <p className="mt-1 max-w-3xl text-sm text-slate-600">
            This document could not be read reliably, so each finding describes what one reading
            of the page appeared to show. Any of them may be an artefact of a misreading. Check
            every line against the source image before acting on it.
          </p>
        </div>
      ) : null}

      {GROUPS.map(({ severity, heading }) => {
        const group = rest.filter((f) => f.severity === severity)
        if (group.length === 0) return null
        return (
          <div key={severity}>
            <h3 className="mb-2 text-xs tracking-wide text-ink-500 uppercase">
              {heading} · {group.length}
            </h3>
            <div className="space-y-2">
              {group.map((finding, index) => (
                <FindingRow
                  key={`${finding.rule_code}-${index}`}
                  finding={finding}
                  onLocate={onLocate}
                />
              ))}
            </div>
          </div>
        )
      })}

      {ambiguous.length > 0 ? (
        <div>
          <h3 className="mb-2 text-xs tracking-wide text-ink-500 uppercase">
            Not checked · {ambiguous.length}
          </h3>
          <div className="space-y-2">
            {ambiguous.map((finding, index) => (
              <QuantityAmbiguousCard key={`qa-${index}`} finding={finding} />
            ))}
          </div>
        </div>
      ) : null}

      {unavailable.length > 0 ? (
        <div>
          <h3 className="mb-2 text-xs tracking-wide text-ink-500 uppercase">
            Checks that could not run · {unavailable.length}
          </h3>
          <p className="mb-2 max-w-3xl text-sm text-ink-500">
            These were not performed, because the documents did not carry the values they
            need. They are not passes.
          </p>
          <div className="space-y-2">
            {unavailable.map((finding, index) => (
              <div
                key={`cu-${index}`}
                className="rounded border border-amber-200 bg-amber-50/50 px-4 py-2"
              >
                <div className="flex flex-wrap items-baseline gap-3">
                  <span className="font-mono text-xs text-amber-900">
                    {String((finding.detail as { check?: string }).check ?? 'check')}
                  </span>
                  <span className="flex-1 text-sm text-ink-700">{finding.message}</span>
                  <span className="font-mono text-xs text-ink-400">
                    {[finding.prescribed_ref, finding.billed_ref].filter(Boolean).join(' · ') ||
                      'document'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {info.length > 0 ? (
        <div>
          <button
            type="button"
            onClick={() => setShowInfo(!showInfo)}
            className="text-xs tracking-wide text-ink-500 uppercase hover:text-ink-700"
          >
            {showInfo ? '−' : '+'} Informational · {info.length}
          </button>
          {showInfo ? (
            <div className="mt-2 space-y-2">
              {info.map((finding, index) => (
                <FindingRow key={`info-${index}`} finding={finding} onLocate={onLocate} />
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
