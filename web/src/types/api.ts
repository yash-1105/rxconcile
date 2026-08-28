/**
 * Mirrors the backend pydantic schema in api/rxconcile/models/schema.py.
 *
 * The nullable types here carry meaning and must not be widened with defaults:
 *
 * - `score: number | null` — null under an inconclusive verdict. Zero would read
 *   as "measured, terrible"; null means "not measurable".
 * - `agreement: Record<string, number> | null` — null for a single-run
 *   extraction, where there is no agreement to report.
 * - `ReviewSummary` counts — null when `agreement_measured` is false. Rendering
 *   0 would claim nothing needs review when nothing was checked.
 *
 * There is deliberately no usable model confidence. `confidence` exists on items
 * for the record only and must never be shown as a reliability indicator.
 */

export type Severity = 'critical' | 'warning' | 'info'

export type Verdict = 'match' | 'match_with_warnings' | 'mismatch' | 'inconclusive'

export type UnitsBasis = 'pack' | 'unit'

/** [x0, y0, x1, y1] normalised 0-1 against the preprocessed image. */
export type BBox = [number, number, number, number]

/** Which document a highlight refers to. */
export type DocSide = 'prescription' | 'bill'

/** Per-field agreement ratio across the N extraction runs. Null when N=1. */
export type Agreement = Record<string, number> | null

export interface PrescribedItem {
  item_id: string
  /** Never nulled, deliberately: the evidence a reviewer checks against the image. */
  raw_text: string
  drug_name: string | null
  salt: string | null
  strength_value: number | null
  strength_unit: string | null
  form: string | null
  dose_per_administration: number | null
  frequency_raw: string | null
  duration_raw: string | null
  duration_days: number | null
  route: string | null
  instructions: string | null
  /** Where this line sits on the image. Null when the model could not locate it. */
  bbox: BBox | null
  agreement: Agreement
  /** The model's own score. Retained for the record; never a reliability signal. */
  confidence: number
}

export interface BilledItem {
  item_id: string
  raw_text: string
  drug_name: string | null
  salt: string | null
  strength_value: number | null
  strength_unit: string | null
  form: string | null
  quantity: number | null
  pack_size: string | null
  units_basis: UnitsBasis | null
  unit_price: string | null
  line_total: string | null
  batch_no: string | null
  hsn_code: string | null
  /** Where this line sits on the image. Null when the model could not locate it. */
  bbox: BBox | null
  agreement: Agreement
  confidence: number
}

export interface Prescription {
  patient_name: string | null
  patient_age: string | null
  patient_sex: string | null
  prescriber_name: string | null
  prescriber_reg_no: string | null
  clinic_name: string | null
  date_issued: string | null
  diagnosis_text: string | null
  items: PrescribedItem[]
  overall_legibility: number
  /** Item count each extraction run returned. Differing values mean instability. */
  run_item_counts: number[]
  /** raw_text of lines present in some runs but not all. */
  unstable_lines: string[]
  warnings: string[]
}

export interface PharmacyBill {
  pharmacy_name: string | null
  pharmacy_licence_no: string | null
  bill_no: string | null
  bill_date: string | null
  patient_name: string | null
  items: BilledItem[]
  subtotal: string | null
  tax_total: string | null
  grand_total: string | null
  currency: string
  run_item_counts: number[]
  unstable_lines: string[]
  warnings: string[]
}

export interface Finding {
  rule_code: string
  severity: Severity
  message: string
  /** PrescribedItem.item_id. Null on document-level findings. */
  prescribed_ref: string | null
  /** BilledItem.item_id. Null on document-level findings. */
  billed_ref: string | null
  detail: Record<string, unknown>
}

export interface MatchedPair {
  prescribed_id: string
  billed_id: string
  /** Composite pairing score, 0-1. */
  similarity: number
}

export interface ReviewSummary {
  /** False for a single-run extraction. The counts below are then null. */
  agreement_measured: boolean
  items_needing_review: number | null
  fields_nulled_by_disagreement: number | null
  unstable_line_count: number | null
  /**
   * Rules that could not run because an input was absent. Not nullable: zero
   * genuinely means every check ran. "We checked and found nothing" and "we
   * could not check" are different results and must never render alike.
   */
  checks_unavailable: number
}

export interface ReconciliationResult {
  verdict: Verdict
  /** Null when the verdict is inconclusive. Never coerce to 0. */
  score: number | null
  findings: Finding[]
  matched_pairs: MatchedPair[]
  unmatched_prescribed: string[]
  unmatched_billed: string[]
  prescription: Prescription
  bill: PharmacyBill
  processing_ms: number
  review_summary: ReviewSummary
}

export interface SampleSummary {
  sample_id: string
  label: string
  prescription: string
  bill: string
  note: string | null
}

export interface ApiErrorBody {
  error_code: string
  message: string
  hint: string
}

/** Shape of QUANTITY_AMBIGUOUS.detail, which needs dedicated presentation. */
export interface QuantityAmbiguousDetail {
  expected_units: number
  billed_quantity: number
  units_per_pack: number | null
  pack_size: string | null
  duration_days: number | null
  units_basis: UnitsBasis | null
  basis_method: string
  interpretations: {
    as_units: { billed_units: number; outcome: string | null }
    as_packs: { billed_units: number; outcome: string | null }
  }
}

/** Shape of CHECK_UNAVAILABLE.detail. */
export interface CheckUnavailableDetail {
  check: string
  missing: string[]
  note: string | null
}

/** Shape of ITEM_COUNT_UNSTABLE.detail. */
export interface ItemCountUnstableDetail {
  document: string
  run_item_counts: number[]
  unstable_lines: string[]
}


// --------------------------------------------------------------------------
// Demo session and scan history
// --------------------------------------------------------------------------

export interface DemoSession {
  token: string
  email: string
  name: string
  employee_number: string
  role: 'employee' | 'admin'
}

/** A history row. The full result is fetched only when a row is opened. */
export interface ScanSummary {
  id: number
  created_at: string
  employee_name: string
  employee_number: string
  user_email: string
  role: string
  prescription_filename: string
  bill_filename: string
  verdict: Verdict
  discrepancy_count: number
  critical_count: number
  warning_count: number
  /** Counted separately and never folded into discrepancies. */
  checks_unavailable_count: number
  processing_ms: number
  extraction_runs: number
}

export interface ScanDetail extends ScanSummary {
  result: ReconciliationResult
}
