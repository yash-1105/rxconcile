import { describe, expect, it } from 'vitest'
import {
  claimTotal,
  claimableAmount,
  countDecisions,
  defaultDecision,
  isClaimable,
  type Decisions,
} from './rows'
import type { SpineState } from '../components/Spine'

type Row = Parameters<typeof isClaimable>[0]

function row(
  key: string,
  status: SpineState,
  {
    total = '100.00',
    prescribed = true,
    findings = 1,
  }: { total?: string | null; prescribed?: boolean; findings?: number } = {},
): Row {
  return {
    key,
    status,
    prescribed: prescribed ? ({ item_id: 'rx-1' } as never) : null,
    billed: { item_id: 'bill-1', line_total: total } as never,
    findings: Array.from({ length: findings }, () => ({ rule_code: 'X' })) as never,
    codes: [],
    partial: false,
  } as unknown as Row
}

describe('what can be claimed', () => {
  it('a matched medicine is claimable', () => {
    expect(isClaimable(row('a', 'clean'))).toBe(true)
    expect(claimableAmount(row('a', 'clean'))).toBe(100)
  })

  it('a brand substitution is still a match, so still claimable', () => {
    expect(isClaimable(row('a', 'substitution'))).toBe(true)
  })

  it('an unprescribed line is never claimable', () => {
    expect(isClaimable(row('a', 'problem', { prescribed: false }))).toBe(false)
  })

  it('a non-medicine is never claimable', () => {
    expect(isClaimable(row('a', 'out-of-scope'))).toBe(false)
  })

  it('a line with no printed amount is not claimable', () => {
    expect(isClaimable(row('a', 'clean', { total: null }))).toBe(false)
  })

  it('a panel-covered lab line with no findings is claimable', () => {
    expect(isClaimable(row('a', 'clean', { prescribed: false, findings: 0 }))).toBe(true)
  })
})

describe('default decisions', () => {
  it('matched lines default to accept', () => {
    expect(defaultDecision(row('a', 'clean'))).toBe('accept')
    expect(defaultDecision(row('a', 'substitution'))).toBe('accept')
  })

  it('anything with a problem starts undecided, never pre-accepted', () => {
    expect(defaultDecision(row('a', 'problem'))).toBe('unset')
    expect(defaultDecision(row('a', 'warning'))).toBe('unset')
  })
})

describe('the claim total', () => {
  const rows = [
    row('matched', 'clean'),
    row('substituted', 'substitution', { total: '50.00' }),
    row('problem', 'problem', { total: '70.00' }),
    row('cosmetic', 'out-of-scope', { total: '400.00' }),
    row('unprescribed', 'problem', { total: '30.00', prescribed: false }),
  ]

  it('counts only accepted, claimable lines', () => {
    const decisions: Decisions = {
      matched: { decision: 'accept' },
      substituted: { decision: 'accept' },
      problem: { decision: 'unset' },
      cosmetic: { decision: 'accept' },
      unprescribed: { decision: 'accept' },
    }
    // 100 + 50. The cosmetic and the unprescribed line are accepted and still
    // contribute nothing, because neither is claimable.
    expect(claimTotal(rows, decisions)).toBe(150)
  })

  it('accepting a problem line adds it', () => {
    const decisions: Decisions = { problem: { decision: 'accept' } }
    expect(claimTotal([row('problem', 'problem', { total: '70.00' })], decisions)).toBe(70)
  })

  it('rejecting a matched line removes it from THIS claim', () => {
    const one = [row('matched', 'clean')]
    expect(claimTotal(one, { matched: { decision: 'accept' } })).toBe(100)
    expect(claimTotal(one, { matched: { decision: 'reject' } })).toBe(0)
  })

  it('falls back to the default when a row has no recorded decision', () => {
    expect(claimTotal([row('matched', 'clean')], {})).toBe(100)
    expect(claimTotal([row('problem', 'problem')], {})).toBe(0)
  })

  it('counts decisions over claimable rows only', () => {
    const counts = countDecisions(rows, {
      matched: { decision: 'accept' },
      substituted: { decision: 'reject' },
      problem: { decision: 'unset' },
      cosmetic: { decision: 'accept' },
      unprescribed: { decision: 'accept' },
    })
    expect(counts).toEqual({ accepted: 1, rejected: 1, undecided: 1 })
  })
})
