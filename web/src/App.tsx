import { useEffect, useState } from 'react'
import { ApiError, fetchSamples, reconcile, reconcileSample, sampleImageUrl } from './api/client'
import {
  clearSession,
  loadSession,
  saveSession,
  type Session,
} from './auth/session'
import { Login } from './components/Login'
import { Processing } from './components/Processing'
import { Result } from './components/Result'
import { EmptyState, PageHeader, Shell } from './components/Shell'
import type { View } from './lib/nav'
import { DropZonePair, EmployeeFields, RunsToggle, SamplePicker } from './components/Upload'
import { Dictionary } from './pages/Dictionary'
import { History } from './pages/History'
import { HowItWorks } from './pages/HowItWorks'
import { Overview } from './pages/Overview'
import type { ReconciliationResult, SampleSummary } from './types/api'

type Stage = 'upload' | 'processing' | 'result'

interface Images {
  prescription: string | null
  bill: string | null
}

const restored = loadSession()

export default function App() {
  const [session, setSession] = useState<Session | null>(restored)
  const [view, setView] = useState<View>('overview')

  const [stage, setStage] = useState<Stage>('upload')
  const [prescriptionFile, setPrescriptionFile] = useState<File | null>(null)
  const [billFile, setBillFile] = useState<File | null>(null)
  const [runs, setRuns] = useState(3)
  const [samples, setSamples] = useState<SampleSummary[]>([])
  const [result, setResult] = useState<ReconciliationResult | null>(null)
  const [images, setImages] = useState<Images>({ prescription: null, bill: null })
  const [error, setError] = useState<Error | null>(null)

  // Prefilled from the signed-in account and editable, because the person at
  // the desk is not always the person the account belongs to.
  const [employeeName, setEmployeeName] = useState(restored?.name ?? '')
  const [employeeNumber, setEmployeeNumber] = useState(restored?.employeeNumber ?? '')

  useEffect(() => {
    fetchSamples()
      .then(setSamples)
      .catch(() => setSamples([]))
  }, [])

  if (!session) {
    return (
      <Login
        onSignIn={(next) => {
          saveSession(next)
          setSession(next)
          setEmployeeName(next.name)
          setEmployeeNumber(next.employeeNumber)
          setView('overview')
        }}
      />
    )
  }

  const signOut = () => {
    clearSession()
    setSession(null)
    setResult(null)
    setStage('upload')
    setPrescriptionFile(null)
    setBillFile(null)
    setEmployeeName('')
    setEmployeeNumber('')
  }

  const goToNew = () => {
    setView('new')
    setStage('upload')
  }

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
    setView('new')
    void run(() => reconcileSample(sample.sample_id, runs), {
      prescription: sampleImageUrl(sample.sample_id, 'prescription'),
      bill: sampleImageUrl(sample.sample_id, 'bill'),
    })
  }

  const readyToRun =
    Boolean(prescriptionFile) &&
    Boolean(billFile) &&
    employeeName.trim().length > 0 &&
    employeeNumber.trim().length > 0

  const newReconciliation = (
    <>
      {stage === 'upload' ? (
        <>
          <PageHeader
            title="New reconciliation"
            lede="Add the prescription and the pharmacy bill it was dispensed against."
            actions={<RunsToggle runs={runs} onChange={setRuns} />}
          />

          {error ? (
            <div className="mb-6 rounded bg-surface px-5 py-4">
              <p className="t-data text-flag">
                {error instanceof ApiError ? error.code : 'REQUEST_FAILED'}
              </p>
              <p className="t-body mt-1 font-medium text-ink">{error.message}</p>
              {error instanceof ApiError ? (
                <p className="t-small mt-1 text-muted">{error.hint}</p>
              ) : null}
            </div>
          ) : null}

          <div className="space-y-8">
            <EmployeeFields
              name={employeeName}
              employeeNumber={employeeNumber}
              onNameChange={setEmployeeName}
              onNumberChange={setEmployeeNumber}
            />

            <DropZonePair
              prescription={prescriptionFile}
              bill={billFile}
              onPrescription={setPrescriptionFile}
              onBill={setBillFile}
              onClearPrescription={() => setPrescriptionFile(null)}
              onClearBill={() => setBillFile(null)}
            />

            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={onReconcile}
                disabled={!readyToRun}
                className="rounded bg-seal px-8 py-3 text-sm font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:bg-ink-300"
              >
                Reconcile
              </button>
              {!readyToRun ? (
                <span className="t-small text-muted">
                  Both documents and the employee details are required.
                </span>
              ) : null}
            </div>

            <SamplePicker samples={samples} onPick={onSample} disabled={false} />
          </div>
        </>
      ) : null}

      {stage === 'processing' ? <Processing runs={runs} /> : null}

      {stage === 'result' && result ? (
        <Result
          result={result}
          prescriptionImage={images.prescription}
          billImage={images.bill}
          onReset={() => {
            setStage('upload')
            setResult(null)
            setPrescriptionFile(null)
            setBillFile(null)
          }}
        />
      ) : null}
    </>
  )

  const pages: Record<View, React.ReactNode> = {
    overview: <Overview session={session} onStart={goToNew} />,
    new: newReconciliation,
    history: <History session={session} onStart={goToNew} />,
    dictionary: <Dictionary />,
    how: <HowItWorks />,
  }

  return (
    <Shell
      session={session}
      view={view}
      onNavigate={(next) => {
        setView(next)
        if (next === 'new' && stage === 'result') setStage('result')
      }}
      onSignOut={signOut}
    >
      {pages[view] ?? <EmptyState title="Not found" body="That screen does not exist." />}
    </Shell>
  )
}
