/**
 * Report downloads.
 *
 * Built server-side from the stored scan, so a report always matches the record
 * rather than whatever the browser currently holds. Every format carries the
 * same disclaimer the screen does.
 */

import { useState } from 'react'
import { downloadExport } from '../api/client'

const FORMATS = [
  { id: 'pdf', label: 'PDF', hint: 'Printable report, readable in greyscale' },
  { id: 'xlsx', label: 'Excel', hint: 'One sheet per table, plus a summary' },
  { id: 'json', label: 'JSON', hint: 'Full result for HRMS ingestion' },
] as const

export function ExportBar({ scanId }: { scanId: number | null }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  if (scanId === null) {
    return (
      <p className="t-small text-muted">
        Exports become available once this reconciliation is saved to history.
      </p>
    )
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="t-micro mr-1 text-muted">Export</span>
        {FORMATS.map((format) => (
          <button
            key={format.id}
            type="button"
            title={format.hint}
            disabled={busy !== null}
            onClick={() => {
              setBusy(format.id)
              setError(null)
              downloadExport(scanId, format.id)
                .catch(() => setError(`The ${format.label} export could not be built.`))
                .finally(() => setBusy(null))
            }}
            className="t-small rounded border border-ink-300 px-3 py-1.5 text-ink hover:bg-paper disabled:opacity-50"
          >
            {busy === format.id ? 'Preparing…' : format.label}
          </button>
        ))}
      </div>
      {error ? <p className="t-small mt-2 text-flag">{error}</p> : null}
    </div>
  )
}
