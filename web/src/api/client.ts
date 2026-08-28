import type {
  ApiErrorBody,
  DemoSession,
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
    await fetch(`${BASE_URL}/api/reconcile`, { method: 'POST', body: form }),
  )
}

export async function reconcileSample(
  sampleId: string,
  runs: number,
): Promise<ReconciliationResult> {
  return unwrap<ReconciliationResult>(
    await fetch(`${BASE_URL}/api/reconcile/sample?runs=${runs}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
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

export async function saveScan(payload: ScanCreate): Promise<ScanSummary> {
  return unwrap<ScanSummary>(
    await fetch(`${BASE_URL}/api/scans`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(payload),
    }),
  )
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
