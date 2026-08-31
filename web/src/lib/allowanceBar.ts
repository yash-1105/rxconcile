/**
 * The two figures the results summary draws: what is left, and how the bar
 * splits.
 *
 * Kept out of the component so they can be asserted directly. The balance is
 * the largest number on the screen and the bar is what a reader takes in
 * before anything else, so neither should rest on a visual check alone.
 *
 * Presentation arithmetic only. Nothing here is a claim decision: `used` and
 * `claim` arrive already computed, by the server and by lib/rows.ts.
 */

/**
 * What is left after this claim.
 *
 * Never below zero: an overdrawn allowance has nothing left. It is not money
 * owed back, and a negative figure on this panel would read as exactly that.
 */
export function balanceAfter(annual: number, used: number, claim: number): number {
  return Math.max(0, annual - used - claim)
}

export interface BarSegments {
  /** Percentage of the track already drawn on by earlier claims. */
  used: number
  /** Percentage this claim adds, capped so the two never exceed the track. */
  claim: number
}

/**
 * The bar as proportions of the annual allowance.
 *
 * An overdrawn claim FILLS the track rather than overflowing it — a segment
 * wider than its container would be clipped by the browser and silently read
 * as "exactly full", which is a different statement.
 */
export function barSegments(annual: number, used: number, claim: number): BarSegments {
  if (annual <= 0) return { used: 0, claim: 0 }
  const usedPct = Math.min(100, (used / annual) * 100)
  const claimPct = Math.max(0, Math.min((claim / annual) * 100, 100 - usedPct))
  return { used: usedPct, claim: claimPct }
}
