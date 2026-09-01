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
  /** Line discount as printed. Null means no discount column, not zero. */
  discount: string | null
  line_total: string | null
  batch_no: string | null
  hsn_code: string | null
  /** Where this line sits on the image. Null when the model could not locate it. */
  bbox: BBox | null
  agreement: Agreement
  confidence: number
}

export interface PrescribedTest {
  item_id: string
  /** Never nulled, deliberately: the evidence a reviewer checks against the image. */
  raw_text: string
  test_name: string | null
  panel: string | null
  urgency: string | null
  bbox: BBox | null
  agreement: Agreement
  confidence: number
}

export interface BilledTest {
  item_id: string
  raw_text: string
  test_name: string | null
  panel: string | null
  quantity: number | null
  unit_price: string | null
  line_total: string | null
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
  tests: PrescribedTest[]
  /**
   * Whether the page carries an investigations section at all.
   *
   * The three states are NOT interchangeable and must never render alike:
   * `false` means no tests were ordered; `true` with an empty `tests` array
   * means tests were ordered and could not be read; `null` means the model
   * could not tell. Only the first of those is a clean result.
   */
  investigations_present: boolean | null
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
  gstin: string | null
  pharmacy_address: string | null
  bill_no: string | null
  bill_date: string | null
  patient_name: string | null
  items: BilledItem[]
  /** Lab lines. A lab invoice populates this with `items` empty, and vice versa. */
  tests: BilledTest[]
  subtotal: string | null
  discount_total: string | null
  tax_total: string | null
  grand_total: string | null
  currency: string
  run_item_counts: number[]
  unstable_lines: string[]
  warnings: string[]
}

/**
 * What the dictionary matcher resolved one line to. **Derived, not transcribed.**
 *
 * `PrescribedItem.salt` is what the model read off the page and is usually null,
 * because prescriptions print brands rather than compositions. This is the
 * lookup result for that brand, kept separate so the two are never conflated.
 */
export type ReimbursementCategory =
  | 'eligible'
  | 'not_eligible'
  | 'needs_review'
  | 'non_medicine'

export interface ReimbursementLine {
  item_id: string
  description: string
  /** Null when the bill prints no amount — excluded from totals, never zero. */
  amount: string | null
  category: ReimbursementCategory
  reason: string
  rule_codes: string[]
}

/**
 * Which billed items are supported by the prescription.
 *
 * **Not an insurance determination.** Coverage rules, copay tiers and policy
 * limits appear in neither document and are not modelled. Nothing here
 * approves, settles or rejects anything.
 */
export interface ReimbursementSummary {
  eligible_total: string
  eligible_line_count: number
  not_eligible_total: string
  not_eligible_line_count: number
  needs_review_total: string
  needs_review_line_count: number
  non_medicine_total: string
  non_medicine_line_count: number
  /** Billed lines with no printed amount: excluded from the totals above. */
  lines_without_amount: number
  currency: string
  lines: ReimbursementLine[]
}

export interface CanonicalMatch {
  item_id: string
  side: DocSide
  name: string | null
  salt: string | null
  match_score: number
  /** "unresolved" means looked up and not found — not "never looked up". */
  method: string
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
  /**
   * Every billed line this pair accounts for, primary first. A lab panel is
   * ordered once and billed as several analytes. Absent on records written
   * before the engine reported it.
   */
  covers?: string[]
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

/** What the operator said they were uploading, and what it was about. */
export interface Submission {
  condition: string | null
  description: string | null
  prescription_supplied: boolean
  pharmacy_bill_supplied: boolean
  lab_report_supplied: boolean
  lab_bill_supplied: boolean
}

export interface ReconciliationResult {
  verdict: Verdict
  /** Null when the verdict is inconclusive. Never coerce to 0. */
  score: number | null
  findings: Finding[]
  matched_pairs: MatchedPair[]
  unmatched_prescribed: string[]
  unmatched_billed: string[]
  submission: Submission
  reimbursement: ReimbursementSummary
  canonical: CanonicalMatch[]
  matched_tests: MatchedPair[]
  unmatched_prescribed_tests: string[]
  unmatched_billed_tests: string[]
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
/** Whether one uploaded document could be READ. Never a finding. */
export interface DocumentReadability {
  /** The lab report is absent by design — nothing is extracted from it. */
  slot: 'prescription' | 'pharmacy_bill' | 'lab_bill'
  label: string
  supplied: boolean
  state: 'read' | 'partly_unreadable' | 'unreadable' | 'not_assessed' | 'not_supplied'
  message: string | null
  detail: string[]
}

export type ReviewStatus = 'submitted' | 'under_review' | 'reviewed'

/**
 * What an employee is told about their own submission.
 *
 * Deliberately narrower than `ScanSummary`, and not a subset of it: the server
 * builds this from an allow-list so a field added there cannot appear here.
 * No verdict, no counts, no amounts, no result.
 */
export interface EmployeeScanSummary {
  id: number
  created_at: string
  employee_name: string
  first_name: string
  middle_name: string
  last_name: string
  employee_number: string
  condition: string | null
  description: string | null
  prescription_filename: string
  bill_filename: string
  lab_report_filename: string
  lab_bill_filename: string
  review_status: ReviewStatus
  certified_by_employee: boolean
  certified_at: string | null
}

/**
 * What was read off the submitter's own documents.
 *
 * A transcription, never a comparison. There is no matched status, no pairing
 * and no verdict anywhere in these shapes, and a billed line nobody prescribed
 * has exactly the same fields as one that was.
 */
export interface PrescribedLine {
  name: string | null
  strength: string | null
  form: string | null
  frequency: string | null
  duration: string | null
  raw_text: string
}

export interface PrescriptionContent {
  prescriber: string | null
  clinic: string | null
  date: string | null
  patient_name: string | null
  patient_age: string | null
  patient_sex: string | null
  medicines: PrescribedLine[]
  investigations: string[]
}

export interface BilledLine {
  item: string | null
  batch: string | null
  expiry: string | null
  pack: string | null
  quantity: string | null
  rate: string | null
  amount: string | null
  raw_text: string
}

export interface BillContent {
  name: string | null
  bill_no: string | null
  bill_date: string | null
  lines: BilledLine[]
  subtotal: string | null
  tax: string | null
  grand_total: string | null
  currency: string
}

export interface LabLine {
  test: string | null
  amount: string | null
  raw_text: string
}

export interface LabBillContent {
  name: string | null
  bill_no: string | null
  bill_date: string | null
  tests: LabLine[]
  subtotal: string | null
  tax: string | null
  grand_total: string | null
  currency: string
}

export interface ExtractedContent {
  prescription: PrescriptionContent
  pharmacy_bill: BillContent
  lab_bill: LabBillContent | null
  /** The total on their documents. Never a reimbursable figure. */
  billed_total: string | null
  currency: string
}

export interface EmployeeScanDetail extends EmployeeScanSummary {
  readability: DocumentReadability[]
  content: ExtractedContent | null
}

export interface ScanSummary {
  id: number
  created_at: string
  employee_name: string
  first_name: string
  middle_name: string
  last_name: string
  employee_number: string
  review_status: ReviewStatus
  certified_by_employee: boolean
  certified_at: string | null
  /** Who completed the review, and when. Empty until one is completed. */
  reviewed_by: string
  reviewed_at: string | null
  user_email: string
  role: string
  prescription_filename: string
  bill_filename: string
  condition: string | null
  description: string | null
  /**
   * What the reviewer accepted. Zero until decisions are recorded, which is
   * why the queue renders it against `review_status` rather than printing a
   * bare 0 next to a claim nobody has looked at yet.
   */
  claimed_amount: string
  allowance_year: string
  verdict: Verdict
  discrepancy_count: number
  critical_count: number
  warning_count: number
  /** Counted separately and never folded into discrepancies. */
  checks_unavailable_count: number
  /** Reimbursement total supported by the prescription. */
  eligible_total: string
  currency: string
  processing_ms: number
  extraction_runs: number
}

export interface ScanDetail extends ScanSummary {
  /** Per-line accept/reject as last saved. Absent on records predating review. */
  decisions?: Record<string, { decision: 'accept' | 'reject' | 'unset'; remark?: string }>
  result: ReconciliationResult
}

// --------------------------------------------------------------------------
// Reference data
// --------------------------------------------------------------------------

export interface DictionaryDrug {
  brand_name: string
  salt_composition: string
  common_strengths: string[]
  form: string
  therapeutic_class: string
  schedule: string
}

export interface DictionaryPanel {
  name: string
  components: string[]
  /** How the panel is written on a real prescription. */
  written_as: string[]
}

/**
 * Both reference tables, served from the files the engine reads.
 *
 * Never copied into the frontend: a drifting copy of reference data is worse
 * than a missing screen, because it looks authoritative while disagreeing with
 * what the matcher actually did.
 */
export interface DictionaryResponse {
  warning: string
  drugs: DictionaryDrug[]
  panels: DictionaryPanel[]
  therapeutic_classes: string[]
  schedules: string[]
}

/** An employee's annual allowance and what is left of it. */
export interface AllowanceView {
  employee_number: string
  employee_name: string
  /** Submissions nobody has finished reviewing. A count, never an amount. */
  awaiting_review: number
  year: string
  year_starts: string
  year_ends: string
  annual_amount: string
  used: string
  scans_counted: number
  balance: string
}
