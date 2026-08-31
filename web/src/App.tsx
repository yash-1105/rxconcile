import { useEffect, useState } from 'react'
import type { Decisions } from './lib/rows'
import {
  ApiError,
  fetchSamples,
  reconcile,
  reconcileSample,
  sampleImageUrl,
  saveScan,
  setToken,
  fetchScanImage,
} from './api/client'
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
import {
  ConditionField,
  DescriptionField,
  DocumentGrid,
  EmployeeFields,
  SamplePicker,
} from './components/Upload'
import { DOCUMENT_SLOTS, type DocumentSlot } from './lib/documents'
import { findDuplicateFiles } from './lib/fileIdentity'
import { Dictionary } from './pages/Dictionary'
import { History } from './pages/History'
import { HowItWorks } from './pages/HowItWorks'
import { Overview } from './pages/Overview'
import type { ReconciliationResult, SampleSummary, ScanDetail } from './types/api'

type Stage = 'upload' | 'processing' | 'result'

interface Images {
  prescription: string | null
  bill: string | null
}

const restored = loadSession()
// The token is what the server trusts; the stored session is only a convenience
// for redrawing the shell without a round trip.
if (restored?.token) setToken(restored.token)

export default function App() {
  const [session, setSession] = useState<Session | null>(restored)
  const [view, setView] = useState<View>('overview')

  const [stage, setStage] = useState<Stage>('upload')
  const [docs, setDocs] = useState<Record<DocumentSlot['key'], File | null>>({
    prescription: null,
    bill: null,
    labReport: null,
    labBill: null,
  })
  const prescriptionFile = docs.prescription
  const billFile = docs.bill
  const [condition, setCondition] = useState('')
  const [conditionOther, setConditionOther] = useState('')
  const [description, setDescription] = useState('')

  const [duplicates, setDuplicates] = useState<Array<[string, string]>>([])

  const setDoc = (key: DocumentSlot['key'], file: File | null) => {
    setDocs((current) => {
      const next = { ...current, [key]: file }
      // Checked on every change: the same document in two slots is a slip, and
      // it should be caught here rather than surfacing later as a finding.
      void findDuplicateFiles(
        DOCUMENT_SLOTS.map((slot) => ({
          key: slot.key,
          label: slot.label,
          file: next[slot.key],
        })),
      ).then(setDuplicates)
      return next
    })
  }
  // "Other" means the free text, not the literal word.
  const resolvedCondition = condition === 'Other' ? conditionOther.trim() : condition
  // Always three. The backend still takes a parameter, for tests.
  const runs = 3
  const [samples, setSamples] = useState<SampleSummary[]>([])
  const [result, setResult] = useState<ReconciliationResult | null>(null)
  const [scanId, setScanId] = useState<number | null>(null)
  const [images, setImages] = useState<Images>({ prescription: null, bill: null })
  const [error, setError] = useState<Error | null>(null)

  // Prefilled from the signed-in account and editable, because the person at
  // the desk is not always the person the account belongs to.
  const [employeeName, setEmployeeName] = useState(restored?.name ?? '')
  const [employeeNumber, setEmployeeNumber] = useState(restored?.employeeNumber ?? '')
  /** A reopened history record is read-only: it is a record of what was reported. */
  const [readOnly, setReadOnly] = useState(false)
  const [storedDecisions, setStoredDecisions] = useState<Decisions>({})
  const [historyKey, setHistoryKey] = useState(0)

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
          setToken(next.token)
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
    setToken(null)
    setSession(null)
    setResult(null)
    setStage('upload')
    setDocs({ prescription: null, bill: null, labReport: null, labBill: null })
    setEmployeeName('')
    setEmployeeNumber('')
  }

  const goToNew = () => {
    setView('new')
    setStage('upload')
    setReadOnly(false)
  }

  /** Reopen a stored scan exactly as it was reported. */
  const openScan = (detail: ScanDetail) => {
    setResult(detail.result)
    setScanId(detail.id)
    setStoredDecisions(detail.decisions ?? {})
    // Source pages are stored with the scan now, so a reopened result can show
    // its audit panel instead of an empty one. Fetched with the token rather
    // than linked, because an <img src> cannot carry one.
    setImages({ prescription: null, bill: null })
    void Promise.all([
      fetchScanImage(detail.id, 'prescription'),
      fetchScanImage(detail.id, 'bill'),
    ]).then(([prescription, bill]) => setImages({ prescription, bill }))
    setReadOnly(true)
    setStage('result')
    setView('new')
  }

  const run = async (
    task: () => Promise<ReconciliationResult>,
    next: Images,
    filenames: { prescription: string; bill: string },
    pages: { prescription?: File | null; bill?: File | null; sampleId?: string | null },
  ) => {
    setError(null)
    setReadOnly(false)
    setScanId(null)
    setStoredDecisions({})
    setStage('processing')
    try {
      const outcome = await task()
      setImages(next)
      setResult(outcome)
      setStage('result')
      // Recorded after the fact. A failed save must not lose the result the
      // user is already looking at, so it is reported and otherwise ignored.
      try {
        const saved = await saveScan(
          {
            employee_name: employeeName,
            employee_number: employeeNumber,
            prescription_filename: filenames.prescription,
            bill_filename: filenames.bill,
            condition: resolvedCondition || null,
            description: description.trim() || null,
            extraction_runs: runs,
            result: outcome,
          },
          pages,
        )
        // Exports are built from the stored record, so they need its id.
        setScanId(saved.id)
        setHistoryKey((key) => key + 1)
      } catch {
        setError(new Error('The result is shown below but could not be saved to history.'))
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error(String(caught)))
      setStage('upload')
    }
  }

  const onReconcile = () => {
    if (!prescriptionFile || !billFile) return
    void run(
      () =>
        reconcile(prescriptionFile, billFile, runs, {
          labReport: docs.labReport,
          labBill: docs.labBill,
          condition: resolvedCondition,
          description: description.trim(),
        }),
      {
        prescription: URL.createObjectURL(prescriptionFile),
        bill: URL.createObjectURL(billFile),
      },
      { prescription: prescriptionFile.name, bill: billFile.name },
      { prescription: prescriptionFile, bill: billFile },
    )
  }

  const onSample = (sample: SampleSummary) => {
    setView('new')
    void run(
      () => reconcileSample(sample.sample_id, runs),
      {
        prescription: sampleImageUrl(sample.sample_id, 'prescription'),
        bill: sampleImageUrl(sample.sample_id, 'bill'),
      },
      { prescription: sample.prescription, bill: sample.bill },
      // The server reads the sample pages off disk rather than the client
      // re-uploading files it never held.
      { sampleId: sample.sample_id },
    )
  }

  const readyToRun =
    Boolean(prescriptionFile) &&
    Boolean(billFile) &&
    duplicates.length === 0 &&
    employeeName.trim().length > 0 &&
    employeeNumber.trim().length > 0

  const newReconciliation = (
    <>
      {stage === 'upload' ? (
        <>
          <PageHeader
            title="Verify"
            lede="Add the documents for this claim. Prescriptions and pharmacy bills are required; lab documents are optional."
          />

          {error ? (
            <div className="mb-6 rounded bg-surface px-5 py-4">
              <p className="t-small text-flag">
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

            <ConditionField
              condition={condition}
              otherText={conditionOther}
              onCondition={setCondition}
              onOtherText={setConditionOther}
            />

            <DescriptionField value={description} onChange={setDescription} />

            <DocumentGrid
              files={docs}
              onSelect={(key, file) => setDoc(key, file)}
              onClear={(key) => setDoc(key, null)}
            />

            {duplicates.length > 0 ? (
              <div className="rounded border border-caution bg-ink-50 px-5 py-4">
                <p className="t-body font-medium text-ink">The same file is in two places</p>
                <ul className="t-small mt-1.5 space-y-1 text-muted">
                  {duplicates.map(([left, right]) => (
                    <li key={`${left}-${right}`}>
                      <span className="font-medium text-ink">{left}</span> and{' '}
                      <span className="font-medium text-ink">{right}</span> hold the same
                      document.
                    </li>
                  ))}
                </ul>
                <p className="t-small mt-2 text-muted">
                  Remove one of them, or replace it with the right file. Sending the same
                  document twice would double every line on it.
                </p>
              </div>
            ) : null}

            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={onReconcile}
                disabled={!readyToRun}
                className="rounded bg-seal px-8 py-3 text-sm font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:bg-ink-300"
              >
                Verify
              </button>
              {!readyToRun ? (
                <span className="t-small text-muted">
                  {duplicates.length > 0
                    ? 'Resolve the duplicate file above.'
                    : 'Prescriptions, pharmacy bills and the employee details are required.'}
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
          readOnly={readOnly}
          scanId={scanId}
          employeeNumber={employeeNumber}
          storedDecisions={storedDecisions}
          onReset={() => {
            setStage('upload')
            setResult(null)
            setScanId(null)
            setReadOnly(false)
            setDocs({ prescription: null, bill: null, labReport: null, labBill: null })
            setDuplicates([])
          }}
        />
      ) : null}
    </>
  )

  const pages: Record<View, React.ReactNode> = {
    overview: <Overview key={historyKey} session={session} onStart={goToNew} onOpen={openScan} />,
    new: newReconciliation,
    history: <History key={historyKey} session={session} onStart={goToNew} onOpen={openScan} />,
    dictionary: <Dictionary />,
    how: <HowItWorks />,
  }

  return (
    <Shell
      session={session}
      view={view}
      onNavigate={(next) => {
        setView(next)
        // "Verify" starts a new one. The previous result is not
        // lost by this: every run is saved to history the moment it completes.
        if (next === 'new' && stage === 'result') {
          setStage('upload')
          setResult(null)
        }
      }}
      onSignOut={signOut}
    >
      {pages[view] ?? <EmptyState title="Not found" body="That screen does not exist." />}
    </Shell>
  )
}
