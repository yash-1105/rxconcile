import { describe, expect, it } from 'vitest'
import { barSegments, balanceAfter } from './allowanceBar'

/**
 * The two figures the summary panel draws.
 *
 * Split out of the component so they can be asserted directly: the balance is
 * the largest number on the screen and the bar is what a reader takes in first,
 * and neither is worth trusting to a visual check alone.
 */
describe('the balance after this claim', () => {
  it('is the allowance less what is used and what is claimed here', () => {
    expect(balanceAfter(12000, 0, 796.5)).toBe(11203.5)
    expect(balanceAfter(12000, 796.5, 86)).toBe(11117.5)
  })

  it('never goes below zero', () => {
    // An overdrawn allowance has nothing left. It is not money owed back, and
    // a negative balance on this panel would read as exactly that.
    expect(balanceAfter(12000, 11000, 4000)).toBe(0)
  })
})

describe('the bar', () => {
  it('draws used and this claim as proportions of the allowance', () => {
    expect(barSegments(12000, 0, 796.5)).toEqual({ used: 0, claim: 6.6375 })
  })

  it('puts the two segments side by side without overlapping', () => {
    const { used, claim } = barSegments(12000, 6000, 3000)
    expect(used).toBe(50)
    expect(claim).toBe(25)
    expect(used + claim).toBeLessThanOrEqual(100)
  })

  it('fills the track rather than overflowing it when the claim overdraws', () => {
    const { used, claim } = barSegments(12000, 11000, 4000)
    expect(used + claim).toBe(100)
  })

  it('draws nothing when the allowance is zero, rather than dividing by it', () => {
    expect(barSegments(0, 0, 500)).toEqual({ used: 0, claim: 0 })
  })
})
