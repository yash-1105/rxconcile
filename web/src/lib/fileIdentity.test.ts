import { describe, expect, it } from 'vitest'
import { findDuplicateFiles } from './fileIdentity'

const file = (name: string, content: string) =>
  new File([content], name, { type: 'image/png' })

const slot = (key: string, label: string, f: File | null) => ({ key, label, file: f })

describe('spotting the same document in two slots', () => {
  it('THE CASE: one file used as both pharmacy bill and lab bill', async () => {
    const shared = file('bill.png', 'identical bytes')
    const found = await findDuplicateFiles([
      slot('prescription', 'Prescriptions', file('rx.png', 'a prescription')),
      slot('bill', 'Pharmacy bills', shared),
      slot('labBill', 'Lab bills', shared),
    ])
    expect(found).toEqual([['Pharmacy bills', 'Lab bills']])
  })

  it('catches a renamed copy, which name and size alone would not', async () => {
    const found = await findDuplicateFiles([
      slot('bill', 'Pharmacy bills', file('bill.png', 'same bytes')),
      slot('labBill', 'Lab bills', file('a-different-name.png', 'same bytes')),
    ])
    expect(found).toHaveLength(1)
  })

  it('does not confuse two different files of the same size', async () => {
    const found = await findDuplicateFiles([
      slot('bill', 'Pharmacy bills', file('bill.png', 'aaaa')),
      slot('labBill', 'Lab bills', file('bill.png', 'bbbb')),
    ])
    expect(found).toEqual([])
  })

  it('ignores empty slots', async () => {
    const found = await findDuplicateFiles([
      slot('bill', 'Pharmacy bills', file('bill.png', 'one')),
      slot('labReport', 'Lab reports', null),
      slot('labBill', 'Lab bills', null),
    ])
    expect(found).toEqual([])
  })

  it('reports every colliding pair', async () => {
    const shared = file('x.png', 'same')
    const found = await findDuplicateFiles([
      slot('prescription', 'Prescriptions', shared),
      slot('bill', 'Pharmacy bills', shared),
      slot('labBill', 'Lab bills', shared),
    ])
    expect(found).toHaveLength(3)
  })
})
