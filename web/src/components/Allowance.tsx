/**
 * The annual allowance, what has been drawn, and what is left.
 *
 * The window and the count of scans behind "used so far" are printed, because
 * a figure nobody can check is a figure nobody should trust. The claim itself
 * is derived from the same rows the tables render, so it always equals what is
 * shown accepted on screen.
 */

import type { AllowanceView } from '../types/api'

function money(amount: number | string, currency = 'INR'): string {
  const value = Number(amount)
  return `${currency} ${
    Number.isFinite(value)
      ? value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      : amount
  }`
}

function Figure({
  label,
  value,
  note,
  emphasis = false,
  low = false,
}: {
  label: string
  value: string
  note?: string
  emphasis?: boolean
  low?: boolean
}) {
  return (
    <div className="rounded border border-ink-200 bg-surface px-5 py-4">
      <p className="t-micro text-muted">{label}</p>
      <p className={`t-display mt-1 ${low ? 'text-flag' : emphasis ? 'text-seal' : 'text-ink'}`}>
        {value}
      </p>
      {note ? <p className="t-small mt-1 text-muted">{note}</p> : null}
    </div>
  )
}

export function Allowance({
  allowance,
  claim,
  currency = 'INR',
}: {
  allowance: AllowanceView | null
  claim: number
  currency?: string
}) {
  if (!allowance) {
    return (
      <p className="t-small text-muted">
        The allowance for this employee could not be loaded.
      </p>
    )
  }
  const used = Number(allowance.used)
  const annual = Number(allowance.annual_amount)
  // What is left AFTER this claim, which is the number a reviewer is deciding
  // against. Never below zero: an overdrawn allowance has nothing left, it is
  // not money owed back.
  const remaining = Math.max(0, annual - used - claim)
  const overdrawn = used + claim > annual

  return (
    <div className="space-y-3">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Figure label="Annual allowance" value={money(annual, currency)} />
        <Figure
          label="Used so far"
          value={money(used, currency)}
          note={`${allowance.scans_counted} earlier ${
            allowance.scans_counted === 1 ? 'claim' : 'claims'
          } this year`}
        />
        <Figure label="This claim" value={money(claim, currency)} emphasis note="Accepted lines only" />
        <Figure
          label="Balance remaining"
          value={money(remaining, currency)}
          low={overdrawn}
          note={overdrawn ? 'This claim exceeds the allowance' : 'After this claim'}
        />
      </div>
      <p className="t-small text-muted">
        Allowance year {allowance.year} ({allowance.year_starts} to {allowance.year_ends}).
        Used so far is the total of accepted lines on this employee&rsquo;s earlier claims in
        that window. Rejected lines are not counted.
      </p>
    </div>
  )
}

/**
 * What was excluded from the claim, and why. Informational: these amounts are
 * not part of the claim and never were.
 */
export function Excluded({
  notOnPrescription,
  notMedicine,
  currency = 'INR',
}: {
  notOnPrescription: { total: string; lines: number }
  notMedicine: { total: string; lines: number }
  currency?: string
}) {
  const cards = [
    {
      label: 'Not on prescription',
      body: 'Billed items with nothing on the prescription behind them.',
      ...notOnPrescription,
    },
    {
      label: 'Not a medicine',
      body: 'Cosmetics, devices, supplements and charges.',
      ...notMedicine,
    },
  ].filter((card) => card.lines > 0)

  if (cards.length === 0) return null
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {cards.map((card) => (
        <div key={card.label} className="rounded border border-ink-200 bg-surface px-5 py-4">
          <p className="t-micro text-muted">{card.label}</p>
          <p className="t-title mt-1 text-ink">{money(card.total, currency)}</p>
          <p className="t-small mt-1 text-muted">
            {card.lines} {card.lines === 1 ? 'line' : 'lines'} — excluded from the claim.
          </p>
          <p className="t-small mt-1.5 text-muted">{card.body}</p>
        </div>
      ))}
    </div>
  )
}
