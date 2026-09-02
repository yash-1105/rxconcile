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
  getSubmission,
  openReview,
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
import { Submitted } from './components/Submitted'
import { EmptyState, PageHeader, Shell } from './components/Shell'
import { landingFor, type View } from './lib/nav'
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
import { History, SubmissionHistory } from './pages/History'
import { HowItWorks } from './pages/HowItWorks'
import { EmployeeOverview, Overview } from './pages/Overview'
import { Queue } from './pages/Queue'
import type {
  EmployeeScanDetail,
  ScanSummary,
  ReconciliationResult,
  SampleSummary,
  ScanDetail,
} from './types/api'

/**
 * `submitted` is the employee's terminus. An admin never reaches it and an
 * employee never reaches `result` — they submit, they do not review.
 */
type Stage = 'upload' | 'processing' | 'result' | 'submitted'

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
  const [view, setView] = useState<View>(restored ? landingFor(restored.role) : 'overview')

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
  const [firstName, setFirstName] = useState(restored?.name ?? '')
  const [middleName, setMiddleName] = useState('')
  const [lastName, setLastName] = useState('')
  const [submission, setSubmission] = useState<EmployeeScanDetail | null>(null)
  const [employeeNumber, setEmployeeNumber] = useState(restored?.employeeNumber ?? '')
  /** A reopened history record is read-only: it is a record of what was reported. */
  const [readOnly, setReadOnly] = useState(false)
  const [storedDecisions, setStoredDecisions] = useState<Decisions>({})
  /**
   * The stored record behind the open result: certification, review status,
   * who reviewed it. Held here rather than inside `Result` so that opening a
   * claim, moving it to `under_review` and completing it all update the screen
   * that is already on it, with no refetch in between.
   */
  const [reviewScan, setReviewScan] = useState<ScanSummary | null>(null)
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
          setFirstName(next.name)
          setEmployeeNumber(next.employeeNumber)
          setView(landingFor(next.role))
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
    setFirstName('')
    setMiddleName('')
    setLastName('')
    setEmployeeNumber('')
  }

  /**
   * Whether this account reviews claims or files them.
   *
   * The browser's copy of the role decides what is RENDERED. What is sent is
   * decided server-side from the token, so a tampered value here changes the
   * screen and not the data.
   */
  const reviewer = session.role === 'admin'

  const goToNew = () => {
    setView('new')
    setStage('upload')
    setReadOnly(false)
  }

  /** Reopen a stored scan exactly as it was reported. */
  /** A submitter reopening their own claim. Never the reconciliation. */
  const openSubmission = (detail: EmployeeScanDetail) => {
    setSubmission(detail)
    setStage('submitted')
    setView('new')
  }

  const openScan = (detail: ScanDetail) => {
    setResult(detail.result)
    setScanId(detail.id)
    setStoredDecisions(detail.decisions ?? {})
    setReviewScan(detail)
    // Opening a submission is what starts its review. The server moves only
    // `submitted` claims, so reopening a finished one leaves it finished.
    void openReview(detail.id)
      .then((summary) => setReviewScan(summary))
      .catch(() => {
        // The result is already on screen and is still readable. A status that
        // did not move is worth saying, but not worth hiding the claim over.
        setError(new Error('This claim could not be moved into review.'))
      })
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
    filenames: {
      prescription: string
      bill: string
      labReport: string
      labBill: string
    },
    pages: {
      prescription?: File | null
      bill?: File | null
      labReport?: File | null
      labBill?: File | null
      sampleId?: string | null
    },
  ) => {
    setError(null)
    setReadOnly(false)
    setScanId(null)
    setStoredDecisions({})
    setReviewScan(null)
    setStage('processing')
    try {
      const outcome = await task()
      setImages(next)
      setResult(outcome)
      // An employee never lands on the result. The reconciliation still runs
      // and is still stored — it is simply not theirs to read.
      setStage(reviewer ? 'result' : 'processing')
      // Recorded after the fact. A failed save must not lose the result the
      // user is already looking at, so it is reported and otherwise ignored.
      try {
        const saved = await saveScan(
          {
            first_name: firstName,
            middle_name: middleName,
            last_name: lastName,
            employee_number: employeeNumber,
            prescription_filename: filenames.prescription,
            bill_filename: filenames.bill,
            lab_report_filename: filenames.labReport,
            lab_bill_filename: filenames.labBill,
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
        if (!reviewer) {
          // Fetched back rather than assembled here: the submitter's shape and
          // its readability come from the server, so the browser never has to
          // be trusted to leave the analysis out.
          setSubmission(await getSubmission(saved.id))
          setStage('submitted')
        }
      } catch {
        setError(
          reviewer
            ? new Error('The result is shown below but could not be saved to history.')
            : new Error('Your claim could not be saved. Please try submitting again.'),
        )
        if (!reviewer) setStage('upload')
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
      {
        prescription: prescriptionFile.name,
        bill: billFile.name,
        labReport: docs.labReport?.name ?? '',
        labBill: docs.labBill?.name ?? '',
      },
      // All four are stored now, so a reviewer can look at any of them.
      {
        prescription: prescriptionFile,
        bill: billFile,
        labReport: docs.labReport,
        labBill: docs.labBill,
      },
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
      // A bundled sample is a prescription and a bill; it carries no lab
      // documents, which is not the same as one being left out of the record.
      { prescription: sample.prescription, bill: sample.bill, labReport: '', labBill: '' },
      // The server reads the sample pages off disk rather than the client
      // re-uploading files it never held.
      { sampleId: sample.sample_id },
    )
  }

  const readyToRun =
    Boolean(prescriptionFile) &&
    Boolean(billFile) &&
    duplicates.length === 0 &&
    firstName.trim().length > 0 &&
    employeeNumber.trim().length > 0

  const newReconciliation = (
    <>
      {stage === 'upload' ? (
        <>
          <PageHeader
            title={reviewer ? 'Verify' : 'Submit claim'}
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
              first={firstName}
              middle={middleName}
              last={lastName}
              employeeNumber={employeeNumber}
              onFirst={setFirstName}
              onMiddle={setMiddleName}
              onLast={setLastName}
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
                {reviewer ? 'Verify' : 'Submit'}
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

      {stage === 'submitted' && submission ? (
        <Submitted
          submission={submission}
          onCertified={setSubmission}
          onStartAnother={() => {
            setStage('upload')
            setSubmission(null)
            setResult(null)
            setScanId(null)
            setDocs({ prescription: null, bill: null, labReport: null, labBill: null })
            setDuplicates([])
          }}
        />
      ) : null}

      {/* Reviewers only. An employee never reaches this stage. */}
      {reviewer && stage === 'result' && result ? (
        <Result
          result={result}
          prescriptionImage={images.prescription}
          billImage={images.bill}
          readOnly={readOnly}
          scanId={scanId}
          /* The CLAIMANT's number, not the signed-in reviewer's. The upload
             form prefills `employeeNumber` from the account, which was
             harmless while a reviewer only ever opened their own runs. Opening
             someone else's claim from the queue made it wrong: the allowance
             panel would show the reviewer's own balance beside a decision
             about somebody else's money. */
          employeeNumber={reviewScan?.employee_number ?? employeeNumber}
          storedDecisions={storedDecisions}
          scan={reviewScan}
          onReviewed={(summary) => {
            setReviewScan(summary)
            // History and the queue both read `review_status`, so they are
            // stale the moment a review completes.
            setHistoryKey((key) => key + 1)
          }}
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
    // Two components, not one with the analysis blanked. A submitter's screens
    // never mount a reviewer's, so nothing is one prop away from leaking.
    overview: reviewer ? (
      <Overview key={historyKey} session={session} onStart={goToNew} onOpen={openScan} />
    ) : (
      <EmployeeOverview
        key={historyKey}
        session={session}
        onStart={goToNew}
        onOpen={openSubmission}
      />
    ),
    queue: <Queue key={historyKey} onOpen={openScan} />,
    new: newReconciliation,
    history: reviewer ? (
      <History key={historyKey} session={session} onStart={goToNew} onOpen={openScan} />
    ) : (
      <SubmissionHistory key={historyKey} onStart={goToNew} onOpen={openSubmission} />
    ),
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
        if (next === 'new' && (stage === 'result' || stage === 'submitted')) {
          setStage('upload')
          setResult(null)
          setSubmission(null)
        }
      }}
      onSignOut={signOut}
    >
      {pages[view] ?? <EmptyState title="Not found" body="That screen does not exist." />}
    </Shell>
  )
}
