import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { SampleSummary } from '../types/api'
import { SpineRule } from './Spine'

const ACCEPTED = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp', 'application/pdf']

function isAccepted(file: File): boolean {
  return ACCEPTED.includes(file.type)
}

export function DropZone({
  label,
  file,
  onSelect,
  onClear,
}: {
  label: string
  file: File | null
  onSelect: (file: File) => void
  onClear: () => void
}) {
  const [over, setOver] = useState(false)
  const [rejected, setRejected] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Derived during render; the effect exists only to release the object URL.
  const preview = useMemo(
    () => (file && file.type !== 'application/pdf' ? URL.createObjectURL(file) : null),
    [file],
  )
  useEffect(() => {
    if (!preview) return
    return () => URL.revokeObjectURL(preview)
  }, [preview])

  const accept = useCallback(
    (candidate: File | undefined) => {
      if (!candidate) return
      if (!isAccepted(candidate)) {
        setRejected(`${candidate.type || 'That file type'} is not supported.`)
        return
      }
      setRejected(null)
      onSelect(candidate)
    },
    [onSelect],
  )

  useEffect(() => {
    const onPaste = (event: ClipboardEvent) => {
      const item = Array.from(event.clipboardData?.files ?? [])[0]
      if (item) accept(item)
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
  }, [accept])

  if (file) {
    return (
      <div className="rounded border border-ink-300 bg-white p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs tracking-wide text-ink-500 uppercase">{label}</p>
            <p className="mt-1 truncate font-mono text-sm text-ink-900">{file.name}</p>
            <p className="mt-0.5 font-mono text-xs text-ink-500">
              {(file.size / 1024).toFixed(0)} KB
            </p>
          </div>
          <button
            type="button"
            onClick={onClear}
            className="shrink-0 rounded border border-ink-300 px-2 py-1 text-xs text-ink-600 hover:bg-ink-100"
          >
            Remove
          </button>
        </div>
        <div className="mt-3 flex h-40 items-center justify-center overflow-hidden rounded border border-ink-200 bg-ink-50">
          {preview ? (
            <img src={preview} alt={`${label} preview`} className="max-h-40 object-contain" />
          ) : (
            <span className="font-mono text-xs text-ink-500">PDF · page 1 will be used</span>
          )}
        </div>
      </div>
    )
  }

  return (
    <div>
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          accept(e.dataTransfer.files[0])
        }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click()
        }}
        className={`flex h-64 cursor-pointer flex-col items-center justify-center rounded border-2 border-dashed px-6 text-center transition-colors ${
          over ? 'border-accent bg-accent/5' : 'border-ink-300 bg-white hover:border-ink-400'
        }`}
      >
        <p className="text-sm font-semibold text-ink-700">{label}</p>
        <p className="mt-2 text-sm text-ink-500">Drag and drop, click to browse, or paste</p>
        <p className="mt-4 font-mono text-xs text-ink-400">JPEG · PNG · WebP · PDF</p>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED.join(',')}
        className="hidden"
        onChange={(e) => accept(e.target.files?.[0])}
      />
      {rejected ? <p className="mt-2 text-sm text-red-700">{rejected}</p> : null}
    </div>
  )
}

export function RunsToggle({ runs, onChange }: { runs: number; onChange: (n: number) => void }) {
  return (
    <div>
      <div className="flex items-center gap-2">
        <span className="t-micro text-muted">Runs</span>
        <div className="flex overflow-hidden rounded border border-ink-300">
          {[1, 3].map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => onChange(option)}
              aria-pressed={runs === option}
              className={`t-data px-2.5 py-1 ${
                runs === option ? 'bg-seal text-white' : 'bg-white text-muted hover:bg-ink-100'
              }`}
            >
              {option}
            </button>
          ))}
        </div>
      </div>
      {/* Stated on the screen rather than in a tooltip: someone switching to
          N=1 is giving up the only reliability signal this product has, and
          should not have to hover to find that out. */}
      <p className="t-small mt-2 max-w-md text-muted">
        {runs === 1
          ? 'N=1 reads each document once, so agreement cannot be measured and is reported as not measured rather than as perfect. Faster, and without the reliability check.'
          : 'N=3 reads each document three times and resolves every field by agreement across the runs. That agreement is the reliability signal — the model’s own confidence score carries no information.'}
      </p>
    </div>
  )
}

export function SamplePicker({
  samples,
  onPick,
  disabled,
}: {
  samples: SampleSummary[]
  onPick: (sample: SampleSummary) => void
  disabled: boolean
}) {
  const [open, setOpen] = useState(false)
  if (samples.length === 0) return null
  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="text-sm text-ink-500 underline decoration-ink-300 underline-offset-4 hover:text-ink-800"
      >
        Or try a bundled sample
      </button>
    )
  }
  return (
    <div className="rounded border border-ink-200 bg-white px-5 py-4">
      <div className="flex items-center justify-between">
        <p className="text-xs tracking-wide text-ink-500 uppercase">Bundled samples</p>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="text-xs text-ink-400 hover:text-ink-700"
        >
          Hide
        </button>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        {samples.map((sample) => (
          <button
            key={sample.sample_id}
            type="button"
            disabled={disabled}
            onClick={() => onPick(sample)}
            className="rounded border border-ink-200 px-4 py-3 text-left hover:border-accent hover:bg-accent/5 disabled:opacity-50"
          >
            <p className="text-sm font-semibold text-ink-900">{sample.label}</p>
            <p className="mt-1 font-mono text-xs text-ink-500">{sample.sample_id}</p>
            {sample.note ? <p className="mt-2 text-sm text-ink-500">{sample.note}</p> : null}
          </button>
        ))}
      </div>
    </div>
  )
}


/**
 * Who is running this reconciliation. Prefilled from the signed-in demo account
 * and editable, because in practice the person at the desk is not always the
 * person the account belongs to.
 */
export function EmployeeFields({
  name,
  employeeNumber,
  onNameChange,
  onNumberChange,
}: {
  name: string
  employeeNumber: string
  onNameChange: (value: string) => void
  onNumberChange: (value: string) => void
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <label className="block">
        <span className="t-micro text-muted">Employee name</span>
        <input
          value={name}
          onChange={(event) => onNameChange(event.target.value)}
          required
          className="t-body mt-1.5 w-full rounded bg-surface px-3 py-2 text-ink placeholder:text-ink-400"
          placeholder="Who is running this"
        />
      </label>
      <label className="block">
        <span className="t-micro text-muted">Employee number</span>
        <input
          value={employeeNumber}
          onChange={(event) => onNumberChange(event.target.value)}
          required
          className="t-data mt-1.5 w-full rounded bg-surface px-3 py-2 text-ink placeholder:text-ink-400"
          placeholder="EMP-0000"
        />
      </label>
    </div>
  )
}

/** The two zones with the spine between them: left prescription, right bill. */
export function DropZonePair({
  prescription,
  bill,
  onPrescription,
  onBill,
  onClearPrescription,
  onClearBill,
}: {
  prescription: File | null
  bill: File | null
  onPrescription: (file: File) => void
  onBill: (file: File) => void
  onClearPrescription: () => void
  onClearBill: () => void
}) {
  return (
    <div className="grid items-stretch gap-6 md:grid-cols-[1fr_auto_1fr] md:gap-0">
      <div className="md:pr-8">
        <DropZone
          label="Prescription"
          file={prescription}
          onSelect={onPrescription}
          onClear={onClearPrescription}
        />
      </div>
      <SpineRule className="hidden self-stretch md:block" />
      <div className="md:pl-8">
        <DropZone label="Pharmacy Bill" file={bill} onSelect={onBill} onClear={onClearBill} />
      </div>
    </div>
  )
}
