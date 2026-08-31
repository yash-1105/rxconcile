import type {
  ApiErrorBody,
  DemoSession,
  DictionaryResponse,
  ReconciliationResult,
  SampleSummary,
  ScanDetail,
  ScanSummary,
} from '../types/api'

// Empty means same-origin: requests go to the Vite dev server, which proxies
// them to the API (see vite.config.ts). Set VITE_API_BASE only to point at an
// API somewhere else entirely.
const BASE_URL = import.meta.env['VITE_API_BASE'] ?? ''

export class ApiError extends Error {
  readonly code: string
  readonly hint: string

  constructor(body: ApiErrorBody) {
    super(body.message)
    this.code = body.error_code
    this.hint = body.hint
  }
}

async function unwrap<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T
  let body: ApiErrorBody
  try {
    body = (await response.json()) as ApiErrorBody
  } catch {
    body = {
      error_code: `HTTP_${response.status}`,
      message: response.statusText || 'Request failed.',
      hint:
        'Could not reach the API. Check it is running — ' +
        (BASE_URL || 'requests are proxied by the dev server') +
        ' — and try again.',
    }
  }
  throw new ApiError(body)
}

export async function fetchDictionary(): Promise<DictionaryResponse> {
  return unwrap<DictionaryResponse>(await fetch(`${BASE_URL}/api/dictionary`))
}

export async function fetchSamples(): Promise<SampleSummary[]> {
  return unwrap<SampleSummary[]>(await fetch(`${BASE_URL}/api/samples`))
}

export async function reconcile(
  prescription: File,
  bill: File,
  runs: number,
): Promise<ReconciliationResult> {
  const form = new FormData()
  form.append('prescription', prescription)
  form.append('bill', bill)
  form.append('runs', String(runs))
  return unwrap<ReconciliationResult>(
    await fetch(`${BASE_URL}/api/reconcile`, {
      method: 'POST',
      headers: authHeaders(),
      body: form,
    }),
  )
}

export async function reconcileSample(
  sampleId: string,
  runs: number,
): Promise<ReconciliationResult> {
  return unwrap<ReconciliationResult>(
    await fetch(`${BASE_URL}/api/reconcile/sample?runs=${runs}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ sample_id: sampleId }),
    }),
  )
}

export function sampleImageUrl(sampleId: string, which: 'prescription' | 'bill'): string {
  return `${BASE_URL}/api/samples/${encodeURIComponent(sampleId)}/image/${which}`
}


// --------------------------------------------------------------------------
// Demo session and scan history
// --------------------------------------------------------------------------

let token: string | null = null

export function setToken(next: string | null): void {
  token = next
}

/**
 * The token is all the server is told. It never receives a role from here —
 * the role is looked up server-side from the email the token was issued for,
 * because a role the caller supplies is not a filter.
 */
function authHeaders(): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export async function openDemoSession(
  email: string,
  password: string,
): Promise<DemoSession> {
  return unwrap<DemoSession>(
    await fetch(`${BASE_URL}/api/demo/session`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    }),
  )
}

export interface ScanCreate {
  employee_name: string
  employee_number: string
  prescription_filename: string
  bill_filename: string
  extraction_runs: number
  result: ReconciliationResult
}

/**
 * Save a completed reconciliation, with the pages it was run against.
 *
 * Multipart because the source pages travel with it. The server preprocesses
 * them exactly as extraction did, so the stored image is what the model saw and
 * bounding boxes land correctly on it. Both are optional: a save must never
 * fail for want of an image, and a bundled sample sends its id instead.
 */
export async function saveScan(
  payload: ScanCreate,
  pages?: { prescription?: File | null; bill?: File | null; sampleId?: string | null },
): Promise<ScanSummary> {
  const form = new FormData()
  form.append('payload', JSON.stringify(payload))
  if (pages?.prescription) form.append('prescription', pages.prescription)
  if (pages?.bill) form.append('bill', pages.bill)
  if (pages?.sampleId) form.append('sample_id', pages.sampleId)
  return unwrap<ScanSummary>(
    await fetch(`${BASE_URL}/api/scans`, {
      method: 'POST',
      headers: authHeaders(),
      body: form,
    }),
  )
}

/** Fetch a report and hand it to the browser as a download. */
export async function downloadExport(
  scanId: number,
  format: 'pdf' | 'xlsx' | 'json',
): Promise<void> {
  const response = await fetch(`${BASE_URL}/api/scans/${scanId}/export.${format}`, {
    headers: authHeaders(),
  })
  if (!response.ok) throw new Error(`export failed: ${response.status}`)
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download =
    response.headers
      .get('content-disposition')
      ?.match(/filename="([^"]+)"/)?.[1] ?? `rxconcile-${scanId}.${format}`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

/**
 * A stored source page as an object URL, for the audit panel on a reopened scan.
 *
 * Fetched rather than pointed at: the endpoint needs the bearer token, and an
 * `<img src>` cannot carry one. Returns null when the scan predates stored
 * pages, which is a normal outcome, not an error.
 */
export async function fetchScanImage(
  scanId: number,
  which: 'prescription' | 'bill',
): Promise<string | null> {
  const response = await fetch(`${BASE_URL}/api/scans/${scanId}/image/${which}`, {
    headers: authHeaders(),
  })
  if (!response.ok) return null
  return URL.createObjectURL(await response.blob())
}

export async function listScans(): Promise<ScanSummary[]> {
  return unwrap<ScanSummary[]>(
    await fetch(`${BASE_URL}/api/scans`, { headers: authHeaders() }),
  )
}

export async function getScan(id: number): Promise<ScanDetail> {
  return unwrap<ScanDetail>(
    await fetch(`${BASE_URL}/api/scans/${id}`, { headers: authHeaders() }),
  )
}

export async function deleteScan(id: number): Promise<void> {
  await unwrap<{ deleted: number }>(
    await fetch(`${BASE_URL}/api/scans/${id}`, {
      method: 'DELETE',
      headers: authHeaders(),
    }),
  )
}
