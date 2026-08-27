import { useEffect, useState } from 'react'
import { ApiError, fetchSamples, reconcile, reconcileSample, sampleImageUrl } from './api/client'
import { Processing } from './components/Processing'
import { Result } from './components/Result'
import { DropZone, RunsToggle, SamplePicker } from './components/Upload'
import type { ReconciliationResult, SampleSummary } from './types/api'

type Stage = 'upload' | 'processing' | 'result'

interface Images {
  prescription: string | null
  bill: string | null
}

export default function App() {
  const [stage, setStage] = useState<Stage>('upload')
  const [prescriptionFile, setPrescriptionFile] = useState<File | null>(null)
  const [billFile, setBillFile] = useState<File | null>(null)
  const [runs, setRuns] = useState(3)
  const [samples, setSamples] = useState<SampleSummary[]>([])
  const [result, setResult] = useState<ReconciliationResult | null>(null)
  const [images, setImages] = useState<Images>({ prescription: null, bill: null })
  const [error, setError] = useState<ApiError | Error | null>(null)

  useEffect(() => {
    fetchSamples()
      .then(setSamples)
      .catch(() => setSamples([]))
  }, [])

  const run = async (task: () => Promise<ReconciliationResult>, next: Images) => {
    setError(null)
    setStage('processing')
    try {
      const outcome = await task()
      setImages(next)
      setResult(outcome)
      setStage('result')
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error(String(caught)))
      setStage('upload')
    }
  }

  const onReconcile = () => {
    if (!prescriptionFile || !billFile) return
    void run(() => reconcile(prescriptionFile, billFile, runs), {
      prescription: URL.createObjectURL(prescriptionFile),
      bill: URL.createObjectURL(billFile),
    })
  }

  const onSample = (sample: SampleSummary) => {
    void run(() => reconcileSample(sample.sample_id, runs), {
      prescription: sampleImageUrl(sample.sample_id, 'prescription'),
      bill: sampleImageUrl(sample.sample_id, 'bill'),
    })
  }

  const reset = () => {
    setStage('upload')
    setResult(null)
    setPrescriptionFile(null)
    setBillFile(null)
    setError(null)
  }

  return (
    <div className="flex min-h-full flex-col">
      <header className="border-b border-ink-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-baseline justify-between px-6 py-4">
          <div className="flex items-baseline gap-3">
            <h1 className="font-mono text-lg font-semibold tracking-tight text-ink-900">
              rxconcile
            </h1>
            <span className="text-sm text-ink-500">
              prescription / pharmacy bill reconciliation
            </span>
          </div>
          <span className="font-mono text-xs text-ink-400">proof of concept</span>
        </div>
      </header>

      <main className="mx-auto w-full max-w-7xl flex-1 px-6 py-8">
        {error ? (
          <div className="mb-6 rounded border border-red-300 bg-red-50 px-5 py-4">
            <p className="font-mono text-xs text-red-800">
              {error instanceof ApiError ? error.code : 'REQUEST_FAILED'}
            </p>
            <p className="mt-1 text-sm font-semibold text-red-900">{error.message}</p>
            {error instanceof ApiError ? (
              <p className="mt-1 text-sm text-red-800">{error.hint}</p>
            ) : null}
          </div>
        ) : null}

        {stage === 'upload' ? (
          <div className="space-y-6">
            <div className="grid gap-6 md:grid-cols-2">
              <DropZone
                label="Prescription"
                file={prescriptionFile}
                onSelect={setPrescriptionFile}
                onClear={() => setPrescriptionFile(null)}
              />
              <DropZone
                label="Pharmacy Bill"
                file={billFile}
                onSelect={setBillFile}
                onClear={() => setBillFile(null)}
              />
            </div>

            <RunsToggle runs={runs} onChange={setRuns} />

            <div>
              <button
                type="button"
                onClick={onReconcile}
                disabled={!prescriptionFile || !billFile}
                className="rounded bg-accent px-6 py-2.5 text-sm font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:bg-ink-300"
              >
                Reconcile
              </button>
              {!prescriptionFile || !billFile ? (
                <span className="ml-3 text-sm text-ink-500">
                  Both documents are required.
                </span>
              ) : null}
            </div>

            <SamplePicker samples={samples} onPick={onSample} disabled={false} />
          </div>
        ) : null}

        {stage === 'processing' ? <Processing runs={runs} /> : null}

        {stage === 'result' && result ? (
          <Result
            result={result}
            prescriptionImage={images.prescription}
            billImage={images.bill}
            onReset={reset}
          />
        ) : null}
      </main>

      <footer className="border-t border-ink-200 bg-white">
        <div className="mx-auto max-w-7xl px-6 py-4">
          <p className="text-xs text-ink-500">
            Proof of concept. Automated document comparison only, not clinical verification.
            All findings require human review.
          </p>
        </div>
      </footer>
    </div>
  )
}
