import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { SampleSummary } from '../types/api'

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
    <div className="rounded border border-ink-200 bg-white px-5 py-4">
      <div className="flex flex-wrap items-center gap-4">
        <span className="text-xs tracking-wide text-ink-500 uppercase">Extraction runs</span>
        <div className="flex overflow-hidden rounded border border-ink-300">
          {[1, 3].map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => onChange(option)}
              className={`px-4 py-1.5 font-mono text-sm ${
                runs === option
                  ? 'bg-accent text-white'
                  : 'bg-white text-ink-600 hover:bg-ink-100'
              }`}
            >
              N={option}
            </button>
          ))}
        </div>
      </div>
      <p className="mt-3 max-w-2xl text-sm text-ink-500">
        {runs === 1 ? (
          <>
            <span className="font-semibold text-amber-700">
              N=1 disables agreement measurement.
            </span>{' '}
            A single run cannot show whether a field is reproducible, so agreement is reported
            as not measured rather than as perfect. For cheap iteration only.
          </>
        ) : (
          <>
            Each document is extracted three times and resolved by per-field agreement. This is
            the only reliability signal available — the model&rsquo;s own confidence score was
            measured to carry no information.
          </>
        )}
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
  if (samples.length === 0) return null
  return (
    <div className="rounded border border-ink-200 bg-white px-5 py-4">
      <p className="text-xs tracking-wide text-ink-500 uppercase">Bundled samples</p>
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
