import type {
  BilledItem,
  Finding,
  PrescribedItem,
  ReconciliationResult,
} from '../types/api'
import { Value } from './primitives'

type RowStatus = 'ok' | 'warning' | 'critical'

interface Row {
  key: string
  prescribed: PrescribedItem | null
  billed: BilledItem | null
  similarity: number | null
  status: RowStatus
  codes: string[]
}

function strengthOf(item: PrescribedItem | BilledItem | null): string | null {
  if (!item || item.strength_value === null) return null
  return `${item.strength_value}${item.strength_unit ?? ''}`
}

function qtyOf(item: BilledItem | null): string | null {
  if (!item || item.quantity === null) return null
  const basis = item.units_basis ? ` ${item.units_basis}` : ''
  const pack = item.pack_size ? ` / ${item.pack_size}` : ''
  return `${item.quantity}${basis}${pack}`
}

function statusFrom(codes: string[]): RowStatus {
  if (codes.some((c) => CRITICAL_CODES.has(c))) return 'critical'
  if (codes.some((c) => WARNING_CODES.has(c))) return 'warning'
  return 'ok'
}

const CRITICAL_CODES = new Set([
  'RX_NOT_BILLED',
  'BILL_NOT_PRESCRIBED',
  'STRENGTH_MISMATCH',
  'SALT_DIFFERENT_CLASS',
  'SCHEDULE_H_UNBACKED',
])
const WARNING_CODES = new Set([
  'FORM_MISMATCH',
  'QUANTITY_SHORT',
  'QUANTITY_EXCESS',
  'DUPLICATE_THERAPY',
])

const STATUS: Record<RowStatus, { label: string; dot: string; text: string }> = {
  ok: { label: 'Matches', dot: 'bg-emerald-500', text: 'text-ink-500' },
  warning: { label: 'Check', dot: 'bg-amber-500', text: 'text-amber-800' },
  critical: { label: 'Problem', dot: 'bg-red-600', text: 'text-red-800' },
}

function buildRows(result: ReconciliationResult): Row[] {
  const rx = new Map(result.prescription.items.map((i) => [i.item_id, i]))
  const bill = new Map(result.bill.items.map((i) => [i.item_id, i]))
  const codesFor = (finding: Finding, id: string) =>
    finding.prescribed_ref === id || finding.billed_ref === id

  const rows: Row[] = result.matched_pairs.map((pair) => {
    const codes = result.findings
      .filter((f) => f.prescribed_ref === pair.prescribed_id && f.billed_ref === pair.billed_id)
      .map((f) => f.rule_code)
    return {
      key: `${pair.prescribed_id}-${pair.billed_id}`,
      prescribed: rx.get(pair.prescribed_id) ?? null,
      billed: bill.get(pair.billed_id) ?? null,
      similarity: pair.similarity,
      status: statusFrom(codes),
      codes,
    }
  })

  for (const id of result.unmatched_prescribed) {
    const codes = result.findings.filter((f) => codesFor(f, id)).map((f) => f.rule_code)
    rows.push({
      key: `rx-only-${id}`,
      prescribed: rx.get(id) ?? null,
      billed: null,
      similarity: null,
      status: statusFrom(codes.length ? codes : ['RX_NOT_BILLED']),
      codes,
    })
  }
  for (const id of result.unmatched_billed) {
    const codes = result.findings.filter((f) => codesFor(f, id)).map((f) => f.rule_code)
    rows.push({
      key: `bill-only-${id}`,
      prescribed: null,
      billed: bill.get(id) ?? null,
      similarity: null,
      status: statusFrom(codes),
      codes,
    })
  }
  return rows
}

function Cell({ item }: { item: PrescribedItem | BilledItem | null }) {
  if (!item) {
    return <span className="font-mono text-xs text-ink-300">not present</span>
  }
  return (
    <span className="font-mono text-xs text-ink-500" title={item.raw_text}>
      {item.item_id}
    </span>
  )
}

export function ComparisonTable({
  result,
  onHover,
  technical = false,
}: {
  result: ReconciliationResult
  onHover?: (row: { prescribedId: string | null; billedId: string | null } | null) => void
  /** Ids and similarity are diagnostics; they belong behind the toggle. */
  technical?: boolean
}) {
  const rows = buildRows(result)
  const headings = technical
    ? ['Status', 'Rx', 'Bill', 'Drug', 'Strength', 'Form', 'Qty', 'Match']
    : ['Status', 'Drug', 'Strength', 'Form', 'Qty']
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[38rem] border-collapse text-sm">
        <thead>
          <tr className="border-b border-ink-300 text-left">
            {headings.map((heading) => (
              <th
                key={heading}
                className="px-3 py-2 text-xs font-semibold tracking-wide text-ink-500 uppercase"
              >
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const mark = STATUS[row.status]
            const drugMismatch =
              row.prescribed && row.billed
                ? (row.prescribed.drug_name ?? '').toLowerCase() !==
                  (row.billed.drug_name ?? '').toLowerCase()
                : false
            const strengthMismatch = row.codes.includes('STRENGTH_MISMATCH')
            const formMismatch = row.codes.includes('FORM_MISMATCH')
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
                className={`border-b border-ink-200 align-top hover:bg-ink-50 ${
                  row.status === 'ok' ? 'text-ink-400' : ''
                }`}
              >
                <td className="px-3 py-2 whitespace-nowrap">
                  <span className="inline-flex items-center gap-2">
                    <span className={`h-2 w-2 rounded-full ${mark.dot}`} />
                    <span className={`text-xs ${mark.text}`}>{mark.label}</span>
                  </span>
                </td>
                {technical ? (
                  <>
                    <td className="px-3 py-2">
                      <Cell item={row.prescribed} />
                    </td>
                    <td className="px-3 py-2">
                      <Cell item={row.billed} />
                    </td>
                  </>
                ) : null}
                <td className="px-3 py-2">
                  <div className={drugMismatch ? 'rounded bg-amber-50 px-1' : ''}>
                    <Value>{row.prescribed?.drug_name}</Value>
                    {row.billed ? (
                      <>
                        <span className="px-1 text-ink-300">/</span>
                        <Value>{row.billed.drug_name}</Value>
                      </>
                    ) : null}
                  </div>
                </td>
                <td className="px-3 py-2">
                  <div className={strengthMismatch ? 'rounded bg-red-50 px-1' : ''}>
                    <Value>{strengthOf(row.prescribed)}</Value>
                    {row.billed ? (
                      <>
                        <span className="px-1 text-ink-300">/</span>
                        <Value>{strengthOf(row.billed)}</Value>
                      </>
                    ) : null}
                  </div>
                </td>
                <td className="px-3 py-2">
                  <div className={formMismatch ? 'rounded bg-amber-50 px-1' : ''}>
                    <Value>{row.prescribed?.form}</Value>
                    {row.billed ? (
                      <>
                        <span className="px-1 text-ink-300">/</span>
                        <Value>{row.billed.form}</Value>
                      </>
                    ) : null}
                  </div>
                </td>
                <td className="px-3 py-2">
                  <Value>{qtyOf(row.billed)}</Value>
                </td>
                {technical ? (
                  <td className="px-3 py-2">
                    {row.similarity === null ? (
                      <span className="font-mono text-xs text-ink-300">—</span>
                    ) : (
                      <span className="font-mono text-xs text-ink-600">
                        {row.similarity.toFixed(2)}
                      </span>
                    )}
                  </td>
                ) : null}
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="mt-3 text-xs text-ink-500">
        Each row shows the prescribed value and the billed value side by side. Cells are
        highlighted only where a rule fired, not merely where the text differs.
      </p>
    </div>
  )
}
