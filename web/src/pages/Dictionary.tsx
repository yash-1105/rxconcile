/**
 * The reference data the engine matches against.
 *
 * Served by GET /api/dictionary, read from the same files the matcher reads.
 * Never copied into the frontend: a drifting copy of reference data is worse
 * than a missing screen, because it looks authoritative while disagreeing with
 * what the matcher actually did.
 *
 * This is the screen a client is most likely to mistake for an authoritative
 * drug database, so the warning is prominent, permanent and not dismissable.
 */

import { useEffect, useMemo, useState } from 'react'
import { fetchDictionary } from '../api/client'
import { PageHeader } from '../components/Shell'
import type { DictionaryResponse } from '../types/api'

function Field({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (next: string) => void
  options: readonly string[]
}) {
  return (
    <label className="block">
      <span className="t-micro text-muted">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="t-small mt-1.5 w-full rounded border border-ink-300 bg-surface px-2.5 py-1.5 text-ink"
      >
        <option value="">All</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {option.replaceAll('_', ' ')}
          </option>
        ))}
      </select>
    </label>
  )
}

export function Dictionary() {
  const [data, setData] = useState<DictionaryResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [therapeuticClass, setTherapeuticClass] = useState('')
  const [schedule, setSchedule] = useState('')

  useEffect(() => {
    fetchDictionary()
      .then(setData)
      .catch(() => setError('The reference data could not be loaded.'))
  }, [])

  const drugs = useMemo(() => {
    if (!data) return []
    const needle = query.trim().toLowerCase()
    return data.drugs.filter((drug) => {
      // Search spans brand and salt: a reviewer looking up "paracetamol"
      // wants every brand that is one.
      const matches =
        needle === '' ||
        drug.brand_name.toLowerCase().includes(needle) ||
        drug.salt_composition.toLowerCase().includes(needle)
      return (
        matches &&
        (therapeuticClass === '' || drug.therapeutic_class === therapeuticClass) &&
        (schedule === '' || drug.schedule === schedule)
      )
    })
  }, [data, query, therapeuticClass, schedule])

  return (
    <>
      <PageHeader
        title="Medicine dictionary"
        lede="The lists the engine matches against: brands to salts, so a generic substitution is recognised rather than reported as a missing item, and lab panels to the analytes a bill itemises them into."
      />

      <p className="t-small text-muted">Reference data for demonstration.</p>
      {error ? <p className="t-small mt-6 text-flag">{error}</p> : null}

      {data ? (
        <>
          <div className="mt-8 grid items-end gap-4 sm:grid-cols-[1fr_14rem_10rem]">
            <label className="block">
              <span className="t-micro text-muted">Search brand or salt</span>
              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="paracetamol, Dolo, pantoprazole…"
                className="t-small mt-1.5 w-full rounded border border-ink-300 bg-surface px-3 py-1.5 text-ink placeholder:text-ink-400"
              />
            </label>
            <Field
              label="Therapeutic class"
              value={therapeuticClass}
              onChange={setTherapeuticClass}
              options={data.therapeutic_classes}
            />
            <Field
              label="Schedule"
              value={schedule}
              onChange={setSchedule}
              options={data.schedules}
            />
          </div>

          <p className="t-small mt-3 text-muted">
            {drugs.length} of {data.drugs.length} brands
          </p>

          <div className="mt-3 overflow-x-auto rounded border border-ink-200 bg-surface">
            <table className="w-full min-w-[52rem] border-collapse">
              <thead>
                <tr className="border-b border-ink-200">
                  {['Brand', 'Salt', 'Common strengths', 'Form', 'Class', 'Schedule'].map(
                    (head) => (
                      <th key={head} className="t-micro px-4 py-3.5 text-left text-muted">
                        {head}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {drugs.map((drug) => (
                  <tr key={drug.brand_name} className="border-b border-ink-100 last:border-b-0">
                    <td className="t-data px-4 py-3.5 text-ink">{drug.brand_name}</td>
                    <td className="t-data px-4 py-3.5 text-muted">{drug.salt_composition}</td>
                    <td className="t-data px-4 py-3.5 text-muted">
                      {drug.common_strengths.join(', ') || '—'}
                    </td>
                    <td className="t-data px-4 py-3.5 text-muted">{drug.form || '—'}</td>
                    <td className="t-small px-4 py-3.5 text-muted">
                      {drug.therapeutic_class.replaceAll('_', ' ') || '—'}
                    </td>
                    <td className="t-data px-4 py-3.5">
                      <span
                        className={
                          drug.schedule === 'OTC' ? 'text-muted' : 'font-semibold text-ink'
                        }
                        title={
                          drug.schedule === 'OTC'
                            ? 'Sold over the counter'
                            : 'Prescription-only. Powers the unbacked-dispensing check.'
                        }
                      >
                        {drug.schedule}
                      </span>
                    </td>
                  </tr>
                ))}
                {drugs.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="t-small px-4 py-6 text-muted">
                      No brand matches that search.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>

          <h2 className="t-micro mt-10 text-muted">Lab panels</h2>
          <p className="t-small mt-1 max-w-3xl text-muted">
            A prescription orders <span className="font-medium text-ink">LFT</span>; the laboratory bills seven
            analytes. Without this table that reads as one test never performed plus seven never
            ordered — seven findings against a correct bill.
          </p>
          <div className="mt-3 overflow-x-auto rounded border border-ink-200 bg-surface">
            <table className="w-full min-w-[44rem] border-collapse">
              <thead>
                <tr className="border-b border-ink-200">
                  {['Panel', 'Written as', 'Decomposes into'].map((head) => (
                    <th key={head} className="t-micro px-4 py-3.5 text-left text-muted">
                      {head}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.panels.map((panel) => (
                  <tr key={panel.name} className="border-b border-ink-100 last:border-b-0">
                    <td className="t-data px-4 py-3.5 text-ink">{panel.name}</td>
                    <td className="t-data px-4 py-3.5 text-muted">
                      {panel.written_as.join(', ') || '—'}
                    </td>
                    <td className="t-small px-4 py-3.5 text-muted">
                      {panel.components.join(', ')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </>
  )
}
