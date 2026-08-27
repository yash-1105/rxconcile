import type { ApiErrorBody, ReconciliationResult, SampleSummary } from '../types/api'

const BASE_URL = import.meta.env['VITE_API_BASE'] ?? 'http://localhost:8000'

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
      hint: 'Check that the API is running on ' + BASE_URL + ' and try again.',
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
