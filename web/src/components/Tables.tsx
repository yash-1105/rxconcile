/**
 * The two comparison tables: medicines and lab tests.
 *
 * Each attribute splits into a prescribed column and a billed column under one
 * grouped heading, so the table reads as two halves rather than as eleven
 * unrelated columns. Rows with nothing wrong recede: the eye should land on the
 * flagged rows first.
 *
 * Presentation only. Every value shown is read from the response as computed —
 * nothing here derives a quantity, resolves a drug or decides a status. Where a
 * value is absent it renders as an em-dash, never as a zero and never as a
 * guess.
 */

import { remark, testRemark } from '../lib/phrasing'
import type {
  BilledItem,
  BilledTest,
  Finding,
  PrescribedItem,
  PrescribedTest,
  ReconciliationResult,
} from '../types/api'
import { SpineMark, type SpineState } from './Spine'

const UNCHECKED_CODES = new Set([
  'QUANTITY_AMBIGUOUS',
  'STRENGTH_UNIT_UNSTATED',
  'TEST_UNRESOLVED',
  'CHECK_UNAVAILABLE',
])

/**
 * A row's status, driven by the severity the ENGINE assigned.
 *
 * Deliberately not derived from the rule code alone: BRAND_SUBSTITUTION is an
 * `info`, because a generic dispensed against its brand at the same salt is a
 * legal substitution and not something to flag. Colouring it amber here would
 * contradict the same finding rendered grey in the analysis list above.
 */
function statusFrom(codes: string[], findings: Finding[]): SpineState {
  if (findings.some((f) => f.severity === 'critical')) return 'problem'
  if (findings.some((f) => f.severity === 'warning')) return 'warning'
  if (codes.some((c) => UNCHECKED_CODES.has(c))) return 'unchecked'
  return 'clean'
}

const STATUS_LABEL: Record<SpineState, string> = {
  clean: 'Matches',
  warning: 'Check',
  problem: 'Problem',
  unchecked: 'Not checked',
}

function Val({ children, muted = false }: { children: React.ReactNode; muted?: boolean }) {
  const empty = children === null || children === undefined || children === ''
  if (empty) {
    return (
      <span className="t-data text-unknown" title="Not present on the document">
        —
      </span>
    )
  }
  return <span className={`t-data ${muted ? 'text-muted' : 'text-ink'}`}>{children}</span>
}

function strengthOf(item: PrescribedItem | BilledItem | null): string | null {
  if (!item || item.strength_value === null) return null
  return `${item.strength_value}${item.strength_unit ?? ''}`
}

/**
 * The quantity a course implies, as the ENGINE computed it.
 *
 * Deliberately not derived here. The expected number lives in the detail of
 * whichever quantity rule ran; if no quantity rule ran there is no expectation
 * to show, and the cell stays an em-dash. Recomputing it in the browser would
 * risk showing a number the engine never agreed with.
 */
function expectedQty(findings: Finding[]): string | null {
  for (const found of findings) {
    const value = found.detail['expected_units']
    if (typeof value === 'number') return String(value)
  }
  return null
}

function billedQty(item: BilledItem | null): string | null {
  if (!item || item.quantity === null) return null
  return item.pack_size ? `${item.quantity} · ${item.pack_size}` : String(item.quantity)
}

interface MedRow {
  key: string
  prescribed: PrescribedItem | null
  billed: BilledItem | null
  similarity: number | null
  findings: Finding[]
  codes: string[]
  status: SpineState
}

function medicineRows(result: ReconciliationResult): MedRow[] {
  const rx = new Map(result.prescription.items.map((i) => [i.item_id, i]))
  const bill = new Map(result.bill.items.map((i) => [i.item_id, i]))
  const rows: MedRow[] = []

  const build = (
    key: string,
    prescribed: PrescribedItem | null,
    billed: BilledItem | null,
    similarity: number | null,
    findings: Finding[],
  ): MedRow => {
    const codes = findings.map((f) => f.rule_code)
    return { key, prescribed, billed, similarity, findings, codes, status: statusFrom(codes, findings) }
  }

  for (const pair of result.matched_pairs) {
    const findings = result.findings.filter(
      (f) => f.prescribed_ref === pair.prescribed_id && f.billed_ref === pair.billed_id,
    )
    rows.push(
      build(
        `${pair.prescribed_id}-${pair.billed_id}`,
        rx.get(pair.prescribed_id) ?? null,
        bill.get(pair.billed_id) ?? null,
        pair.similarity,
        findings,
      ),
    )
  }
  for (const id of result.unmatched_prescribed) {
    const findings = result.findings.filter((f) => f.prescribed_ref === id)
    rows.push(build(`rx-only-${id}`, rx.get(id) ?? null, null, null, findings))
  }
  for (const id of result.unmatched_billed) {
    const findings = result.findings.filter((f) => f.billed_ref === id)
    rows.push(build(`bill-only-${id}`, null, bill.get(id) ?? null, null, findings))
  }
  return rows
}

/**
 * The salt behind a row.
 *
 * Prefers the canonical salt the matcher resolved (carried on a
 * BRAND_SUBSTITUTION or DUPLICATE_THERAPY finding), then the salt transcribed
 * off either document. When the drug was never resolved and neither page prints
 * a salt, the cell is an em-dash — never an inference from the brand name.
 */
function saltOf(row: MedRow): string | null {
  for (const found of row.findings) {
    const value = found.detail['salt']
    if (typeof value === 'string' && value) return value
  }
  return row.prescribed?.salt ?? row.billed?.salt ?? null
}

function GroupHead({
  label,
  span = 2,
}: {
  label: string
  span?: number
}) {
  return (
    <th
      colSpan={span}
      scope="colgroup"
      className="t-micro border-b border-l border-ink-200 px-3 pt-2 pb-1 text-center text-muted first:border-l-0"
    >
      {label}
    </th>
  )
}

function SubHead({ label, side }: { label: string; side?: 'rx' | 'bill' }) {
  return (
    <th
      scope="col"
      className={`t-micro px-3 pb-2 text-left font-normal text-muted ${
        side === 'rx' ? 'border-l border-ink-200' : ''
      }`}
    >
      {label}
    </th>
  )
}

function StatusCell({ status }: { status: SpineState }) {
  return (
    <td className="px-3 py-2.5 align-top whitespace-nowrap">
      <span className="inline-flex items-center gap-2">
        <SpineMark state={status} />
        <span className="t-micro text-muted">{STATUS_LABEL[status]}</span>
      </span>
    </td>
  )
}

function RemarkCell({ text }: { text: string }) {
  return (
    <td className="min-w-[13rem] border-l border-ink-200 px-3 py-2.5 align-top">
      {text ? (
        <span className="t-small text-ink">{text}</span>
      ) : (
        <span className="t-data text-unknown">—</span>
      )}
    </td>
  )
}

export function MedicinesTable({
  result,
  onHover,
  technical = false,
}: {
  result: ReconciliationResult
  onHover?: (row: { prescribedId: string | null; billedId: string | null } | null) => void
  technical?: boolean
}) {
  const rows = medicineRows(result)
  if (rows.length === 0) {
    return (
      <p className="t-small text-muted">
        Neither document carries a medicine line. Nothing to compare here.
      </p>
    )
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[62rem] border-collapse">
        <thead>
          <tr>
            <th rowSpan={2} scope="col" className="t-micro px-3 pb-2 text-left text-muted">
              Status
            </th>
            <GroupHead label="Drug" />
            <th rowSpan={2} scope="col" className="t-micro border-l border-ink-200 px-3 pb-2 text-left text-muted">
              Salt
            </th>
            <GroupHead label="Strength" />
            <GroupHead label="Form" />
            <GroupHead label="Quantity" />
            {technical ? <GroupHead label="Ids" /> : null}
            <th rowSpan={2} scope="col" className="t-micro border-l border-ink-200 px-3 pb-2 text-left text-muted">
              Remark
            </th>
          </tr>
          <tr className="border-b border-ink-200">
            <SubHead label="Prescribed" side="rx" />
            <SubHead label="Billed" />
            <SubHead label="Prescribed" side="rx" />
            <SubHead label="Billed" />
            <SubHead label="Prescribed" side="rx" />
            <SubHead label="Billed" />
            <SubHead label="Prescribed" side="rx" />
            <SubHead label="Billed" />
            {technical ? (
              <>
                <SubHead label="Rx" side="rx" />
                <SubHead label="Bill" />
              </>
            ) : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const quiet = row.status === 'clean'
            return (
              <tr
                key={row.key}
                onMouseEnter={() =>
                  onHover?.({
                    prescribedId: row.prescribed?.item_id ?? null,
                    billedId: row.billed?.item_id ?? null,
                  })
                }
                onMouseLeave={() => onHover?.(null)}
                className={`border-b border-ink-200 align-top hover:bg-[color:var(--color-paper)] ${
                  quiet ? 'opacity-65' : ''
                }`}
              >
                <StatusCell status={row.status} />
                <td className="border-l border-ink-200 px-3 py-2.5">
                  <Val muted={quiet}>{row.prescribed?.drug_name}</Val>
                </td>
                <td className="px-3 py-2.5">
                  <Val muted={quiet}>{row.billed?.drug_name}</Val>
                </td>
                <td className="border-l border-ink-200 px-3 py-2.5">
                  <Val muted>{saltOf(row)}</Val>
                </td>
                <td className="border-l border-ink-200 px-3 py-2.5">
                  <Val muted={quiet}>{strengthOf(row.prescribed)}</Val>
                </td>
                <td className="px-3 py-2.5">
                  <Val muted={quiet}>{strengthOf(row.billed)}</Val>
                </td>
                <td className="border-l border-ink-200 px-3 py-2.5">
                  <Val muted={quiet}>{row.prescribed?.form}</Val>
                </td>
                <td className="px-3 py-2.5">
                  <Val muted={quiet}>{row.billed?.form}</Val>
                </td>
                <td className="border-l border-ink-200 px-3 py-2.5">
                  <Val muted={quiet}>{expectedQty(row.findings)}</Val>
                </td>
                <td className="px-3 py-2.5">
                  <Val muted={quiet}>{billedQty(row.billed)}</Val>
                </td>
                {technical ? (
                  <>
                    <td className="border-l border-ink-200 px-3 py-2.5">
                      <span className="t-data text-muted" title={row.prescribed?.raw_text}>
                        {row.prescribed?.item_id ?? '—'}
                      </span>
                    </td>
                    <td className="px-3 py-2.5">
                      <span className="t-data text-muted" title={row.billed?.raw_text}>
                        {row.billed?.item_id ?? '—'}
                        {row.similarity !== null ? ` · ${row.similarity.toFixed(2)}` : ''}
                      </span>
                    </td>
                  </>
                ) : null}
                <RemarkCell text={remark(row.codes, row.findings)} />
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

interface TestRow {
  key: string
  prescribed: PrescribedTest | null
  billed: BilledTest | null
  findings: Finding[]
  codes: string[]
  status: SpineState
}

function testRows(result: ReconciliationResult): TestRow[] {
  // Stored history records predating lab reconciliation are returned as the raw
  // blob they were saved as, with no `tests` arrays at all. Defaulting here
  // keeps an old record readable instead of blanking the screen.
  const rx = new Map((result.prescription.tests ?? []).map((t) => [t.item_id, t]))
  const bill = new Map((result.bill.tests ?? []).map((t) => [t.item_id, t]))
  const rows: TestRow[] = []
  const build = (
    key: string,
    prescribed: PrescribedTest | null,
    billed: BilledTest | null,
    findings: Finding[],
  ): TestRow => {
    const codes = findings.map((f) => f.rule_code)
    return { key, prescribed, billed, findings, codes, status: statusFrom(codes, findings) }
  }

  for (const pair of result.matched_tests ?? []) {
    const findings = result.findings.filter(
      (f) => f.prescribed_ref === pair.prescribed_id && f.billed_ref === pair.billed_id,
    )
    rows.push(
      build(
        `${pair.prescribed_id}-${pair.billed_id}`,
        rx.get(pair.prescribed_id) ?? null,
        bill.get(pair.billed_id) ?? null,
        findings,
      ),
    )
  }
  // A panel match consumes every billed line that covered it, but the response
  // names only the primary one in `matched_tests`. Without this, an ordered CBC
  // billed as six analytes would show one line and silently drop five -- a table
  // that does not account for every line on the bill is worse than no table.
  //
  // They are NOT attributed to a particular ordered panel here: with two matched
  // panels the response does not say which line covered which, and guessing
  // would be an invention. They are shown as what is certain -- billed, and
  // covered by something that was ordered.
  const accounted = new Set([
    ...(result.matched_tests ?? []).map((p) => p.billed_id),
    ...(result.unmatched_billed_tests ?? []),
  ])
  for (const test of result.bill.tests ?? []) {
    if (accounted.has(test.item_id)) continue
    rows.push({
      key: `covered-${test.item_id}`,
      prescribed: null,
      billed: test,
      findings: [],
      codes: [],
      status: 'clean',
    })
  }
  for (const id of result.unmatched_prescribed_tests ?? []) {
    rows.push(
      build(`rxt-${id}`, rx.get(id) ?? null, null, result.findings.filter((f) => f.prescribed_ref === id)),
    )
  }
  for (const id of result.unmatched_billed_tests ?? []) {
    rows.push(
      build(`bt-${id}`, null, bill.get(id) ?? null, result.findings.filter((f) => f.billed_ref === id)),
    )
  }

  return rows
}

/**
 * The panel a row belongs to.
 *
 * Prefers the canonical panel the decomposition resolved, which is what makes
 * one ordered "LFT" and six billed analytes legible as a single thing.
 */
function panelOf(row: TestRow): string | null {
  for (const found of row.findings) {
    const value = found.detail['panel'] ?? found.detail['resolved_as']
    if (typeof value === 'string' && value) return value
  }
  return row.prescribed?.panel ?? row.billed?.panel ?? null
}

export function LabTestsTable({
  result,
  onHover,
  technical = false,
}: {
  result: ReconciliationResult
  onHover?: (row: { prescribedId: string | null; billedId: string | null } | null) => void
  technical?: boolean
}) {
  const rows = testRows(result)
  const coveredCount =
    (result.matched_tests ?? []).length === 1
      ? rows.filter((r) => r.prescribed === null && r.billed !== null && r.findings.length === 0)
          .length
      : 0

  // An empty table reads as a rendering failure or a missed section. These two
  // states are entirely different results and must never render alike.
  if (rows.length === 0) {
    // `undefined` on a legacy record is not the same as a measured `null`, and
    // neither may render as "no tests ordered".
    const present = result.prescription.investigations_present ?? null
    if (result.prescription.tests === undefined) {
      return (
        <p className="t-body text-muted">
          This result was recorded before lab tests were reconciled, so it carries no
          investigations data. Nothing here says tests were or were not ordered.
        </p>
      )
    }
    if (present === true) {
      return (
        <p className="t-body text-ink">
          <strong className="font-semibold">
            The investigations section could not be read.
          </strong>{' '}
          This prescription orders lab work, but no test line could be made out. This is not a
          finding that no tests were ordered — it is a finding that what was ordered is unknown.
        </p>
      )
    }
    if (present === false) {
      return (
        <p className="t-body text-muted">
          No investigations ordered on this prescription. Nothing to compare, and nothing
          missing.
        </p>
      )
    }
    return (
      <p className="t-body text-ink">
        No investigations section was found on this prescription, but its presence could not be
        confirmed. Read the page before treating this as "no tests ordered".
      </p>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] border-collapse">
        <thead>
          <tr>
            <th rowSpan={2} scope="col" className="t-micro px-3 pb-2 text-left text-muted">
              Status
            </th>
            <GroupHead label="Test" />
            <th rowSpan={2} scope="col" className="t-micro border-l border-ink-200 px-3 pb-2 text-left text-muted">
              Panel
            </th>
            {technical ? <GroupHead label="Ids" /> : null}
            <th rowSpan={2} scope="col" className="t-micro border-l border-ink-200 px-3 pb-2 text-left text-muted">
              Remark
            </th>
          </tr>
          <tr className="border-b border-ink-200">
            <SubHead label="Prescribed" side="rx" />
            <SubHead label="Billed" />
            {technical ? (
              <>
                <SubHead label="Rx" side="rx" />
                <SubHead label="Bill" />
              </>
            ) : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const quiet = row.status === 'clean'
            return (
              <tr
                key={row.key}
                onMouseEnter={() =>
                  onHover?.({
                    prescribedId: row.prescribed?.item_id ?? null,
                    billedId: row.billed?.item_id ?? null,
                  })
                }
                onMouseLeave={() => onHover?.(null)}
                className={`border-b border-ink-200 align-top hover:bg-[color:var(--color-paper)] ${
                  quiet ? 'opacity-65' : ''
                }`}
              >
                <StatusCell status={row.status} />
                <td className="border-l border-ink-200 px-3 py-2.5">
                  <Val muted={quiet}>{row.prescribed?.test_name}</Val>
                </td>
                <td className="px-3 py-2.5">
                  <Val muted={quiet}>{row.billed?.test_name}</Val>
                </td>
                <td className="border-l border-ink-200 px-3 py-2.5">
                  <Val muted>{panelOf(row)}</Val>
                </td>
                {technical ? (
                  <>
                    <td className="border-l border-ink-200 px-3 py-2.5">
                      <span className="t-data text-muted" title={row.prescribed?.raw_text}>
                        {row.prescribed?.item_id ?? '—'}
                      </span>
                    </td>
                    <td className="px-3 py-2.5">
                      <span className="t-data text-muted" title={row.billed?.raw_text}>
                        {row.billed?.item_id ?? '—'}
                      </span>
                    </td>
                  </>
                ) : null}
                <RemarkCell
                  text={
                    row.findings.length === 0 && row.prescribed === null && row.billed !== null
                      ? 'Billed as part of an ordered panel'
                      : row.findings.length === 0 && row.prescribed !== null && coveredCount > 0
                        ? `Ordered as a panel — billed as ${coveredCount + 1} itemised lines`
                        : testRemark(row.codes, row.findings)
                  }
                />
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
