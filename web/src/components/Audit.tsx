import { useEffect, useRef, useState } from 'react'
import type { BilledItem, PharmacyBill, PrescribedItem, Prescription } from '../types/api'
import { AgreementBadge } from './primitives'

/** Agreement at or above this renders as a confident box; below it, as uncertain. */
const CONFIDENT_BBOX_AGREEMENT = 1

export function ImageViewer({
  src,
  label,
  items = [],
  highlightId = null,
}: {
  src: string | null
  label: string
  items?: (PrescribedItem | BilledItem)[]
  highlightId?: string | null
}) {
  const [zoom, setZoom] = useState(1)
  const highlightRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    highlightRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [highlightId])

  if (!src) {
    return (
      <div className="flex h-64 items-center justify-center rounded border border-ink-200 bg-ink-100 text-sm text-ink-500">
        No image available for {label}
      </div>
    )
  }
  const located = items.filter((item) => item.bbox !== null)
  const unlocated = items.length - located.length
  return (
    <div className="rounded border border-ink-200 bg-white">
      <div className="flex items-center justify-between border-b border-ink-200 px-3 py-2">
        <span className="text-xs tracking-wide text-ink-500 uppercase">{label}</span>
        <div className="flex items-center gap-1">
          {[
            { symbol: '−', next: () => Math.max(0.5, zoom - 0.25), title: 'Zoom out' },
            { symbol: '＋', next: () => Math.min(6, zoom + 0.25), title: 'Zoom in' },
          ].map(({ symbol, next, title }) => (
            <button
              key={title}
              type="button"
              title={title}
              onClick={() => setZoom(next())}
              className="h-6 w-6 rounded border border-ink-300 font-mono text-xs text-ink-600 hover:bg-ink-100"
            >
              {symbol}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setZoom(1)}
            className="ml-1 rounded border border-ink-300 px-2 py-0.5 font-mono text-xs text-ink-600 hover:bg-ink-100"
          >
            {zoom.toFixed(2)}×
          </button>
        </div>
      </div>
      <div className="h-[28rem] overflow-auto bg-ink-100 p-2">
        <div className="relative max-w-none" style={{ width: `${zoom * 100}%` }}>
          <img src={src} alt={label} className="block w-full" />
          {located.map((item) => {
            const [x0, y0, x1, y1] = item.bbox as [number, number, number, number]
            const ratio = item.agreement?.['bbox'] ?? null
            // A box the runs did not agree on is not a location. It is drawn
            // dashed and pale so it never reads as a precise pointer.
            const confident = ratio === null || ratio >= CONFIDENT_BBOX_AGREEMENT
            const active = highlightId === item.item_id
            return (
              <div
                key={item.item_id}
                ref={active ? highlightRef : undefined}
                title={
                  confident
                    ? item.raw_text
                    : `${item.raw_text}\n\nLocation uncertain: the extraction runs placed this box differently.`
                }
                className={[
                  'pointer-events-none absolute transition-colors',
                  confident ? 'border-2' : 'border-2 border-dashed',
                  active
                    ? 'border-accent bg-accent/20'
                    : confident
                      ? 'border-accent/40'
                      : 'border-amber-500/60 bg-amber-200/10',
                ].join(' ')}
                style={{
                  left: `${x0 * 100}%`,
                  top: `${y0 * 100}%`,
                  width: `${(x1 - x0) * 100}%`,
                  height: `${(y1 - y0) * 100}%`,
                }}
              />
            )
          })}
        </div>
      </div>
      <p className="border-t border-ink-200 px-3 py-2 text-xs text-ink-500">
        Zoom in to check a disputed line against the actual handwriting.
        {unlocated > 0 ? (
          <span className="ml-1 text-amber-700">
            {unlocated} line{unlocated > 1 ? 's' : ''} could not be located on this
            image and {unlocated > 1 ? 'have' : 'has'} no box.
          </span>
        ) : null}
      </p>
    </div>
  )
}

/**
 * Field-level audit. Agreement replaces confidence throughout: a field nulled by
 * disagreement is shown alongside the raw_text it came from, because raw_text is
 * never nulled and is what a reviewer checks against the image.
 */
function ItemAudit({ item }: { item: PrescribedItem | BilledItem }) {
  const agreement = item.agreement
  const fields = Object.entries(item).filter(
    ([key]) =>
      !['item_id', 'raw_text', 'agreement', 'confidence', 'bbox'].includes(key),
  ) as [string, unknown][]
  const nulled = fields.filter(
    ([key, value]) => value === null && agreement !== null && (agreement[key] ?? 1) < 1,
  )

  return (
    <div className="border-b border-ink-200 py-3 last:border-b-0">
      <div className="flex items-baseline gap-3">
        <span className="font-mono text-xs text-ink-500">{item.item_id}</span>
        <span className="flex-1 font-mono text-xs break-words text-ink-800">
          {item.raw_text}
        </span>
        {item.bbox === null ? (
          <span className="shrink-0 font-mono text-xs text-amber-700" title="No bounding box">
            not located
          </span>
        ) : null}
      </div>

      {nulled.length > 0 ? (
        <div className="mt-2 rounded border border-amber-300 bg-amber-50 px-3 py-2">
          <p className="text-xs font-semibold text-amber-900">
            {nulled.length} field{nulled.length > 1 ? 's' : ''} left null because the runs
            disagreed
          </p>
          <p className="mt-1 font-mono text-xs text-amber-900">
            {nulled.map(([key]) => key).join(', ')}
          </p>
          <p className="mt-1 text-xs text-amber-800">
            The transcribed line above is shown in full so you can read the value off the image
            yourself.
          </p>
        </div>
      ) : null}

      <dl className="mt-2 grid gap-x-4 gap-y-0.5 sm:grid-cols-[10rem_1fr_3rem]">
        {fields.map(([key, value]) => {
          const ratio = agreement === null ? null : (agreement[key] ?? null)
          const isNull = value === null
          return (
            <div key={key} className="contents">
              <dt className="font-mono text-xs text-ink-500">{key}</dt>
              <dd
                className={`font-mono text-xs break-all ${isNull ? 'text-ink-300' : 'text-ink-800'}`}
              >
                {isNull ? 'null' : String(value)}
              </dd>
              <dd className="text-right">
                {ratio === null && agreement === null ? (
                  <AgreementBadge ratio={null} />
                ) : ratio !== null ? (
                  <AgreementBadge ratio={ratio} />
                ) : null}
              </dd>
            </div>
          )
        })}
      </dl>
    </div>
  )
}

export function AuditPanel({
  prescription,
  bill,
  prescriptionImage,
  billImage,
  highlight,
}: {
  prescription: Prescription
  bill: PharmacyBill
  prescriptionImage: string | null
  billImage: string | null
  highlight?: { side: 'prescription' | 'bill'; itemId: string } | null
}) {
  const [showJson, setShowJson] = useState(false)
  return (
    <div className="space-y-6">
      <div className="rounded border border-ink-300 bg-white px-5 py-3">
        <p className="text-sm text-ink-600">
          <span className="font-semibold text-ink-800">Agreement, not confidence.</span> The
          ratios below are the share of extraction runs that agreed on each field. The
          model&rsquo;s own confidence score is deliberately not shown: it measured 0.75–0.95
          across every observation and was sometimes highest on fields it could not reproduce.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {[
          {
            label: 'Prescription',
            image: prescriptionImage,
            items: prescription.items as (PrescribedItem | BilledItem)[],
            side: 'prescription' as const,
          },
          {
            label: 'Pharmacy bill',
            image: billImage,
            items: bill.items as (PrescribedItem | BilledItem)[],
            side: 'bill' as const,
          },
        ].map(({ label, image, items, side }) => (
          <div key={label} className="space-y-4">
            <ImageViewer
              src={image}
              label={label}
              items={items}
              highlightId={highlight?.side === side ? highlight.itemId : null}
            />
            <div className="rounded border border-ink-200 bg-white px-4 py-2">
              <p className="py-2 text-xs tracking-wide text-ink-500 uppercase">
                {label} · extracted fields
              </p>
              {items.map((item) => (
                <ItemAudit key={item.item_id} item={item} />
              ))}
            </div>
          </div>
        ))}
      </div>

      <div>
        <button
          type="button"
          onClick={() => setShowJson(!showJson)}
          className="text-xs tracking-wide text-ink-500 uppercase hover:text-ink-700"
        >
          {showJson ? '−' : '+'} Raw extracted JSON
        </button>
        {showJson ? (
          <div className="mt-2 grid gap-4 lg:grid-cols-2">
            {[
              { label: 'prescription', data: prescription },
              { label: 'bill', data: bill },
            ].map(({ label, data }) => (
              <div key={label} className="rounded border border-ink-200 bg-white">
                <p className="border-b border-ink-200 px-3 py-2 font-mono text-xs text-ink-500">
                  {label}
                </p>
                <pre className="max-h-[32rem] overflow-auto px-3 py-2 font-mono text-xs text-ink-700">
                  {JSON.stringify(data, null, 2)}
                </pre>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  )
}
