/**
 * The four documents a claim can carry, and the conditions offered.
 *
 * Kept out of the component file so it exports only components, and so the
 * slots have one definition. Which slot a file went into is not cosmetic: the
 * engine reads it to decide whether a lab bill is missing or was simply not
 * part of this claim.
 */

/** Mirrors CONDITIONS in the API schema. */
export const CONDITIONS: readonly string[] = [
  'Fever / infection',
  'Diabetes',
  'Hypertension',
  'Respiratory',
  'Gastric',
  'Dental',
  'Injury',
  'Skin',
  'Other',
]

export interface DocumentSlot {
  key: 'prescription' | 'bill' | 'labReport' | 'labBill'
  label: string
  hint: string
  required: boolean
}

export const DOCUMENT_SLOTS: readonly DocumentSlot[] = [
  {
    key: 'prescription',
    label: 'Prescriptions',
    hint: 'Upload one file with all prescriptions',
    required: true,
  },
  {
    key: 'bill',
    label: 'Pharmacy bills',
    hint: 'Upload one file with all bills',
    required: true,
  },
  { key: 'labReport', label: 'Lab reports', hint: 'Optional', required: false },
  { key: 'labBill', label: 'Lab bills', hint: 'Optional', required: false },
]
