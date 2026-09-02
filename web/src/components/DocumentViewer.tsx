/**
 * The uploaded documents themselves, page by page, on the review screen.
 *
 * A reviewer deciding a rejection needs to look at the page. Until now a
 * reopened scan had no images at all, and even a fresh one showed only the two
 * documents the audit panel knew about — never a lab report, never page 4 of 6.
 *
 * These are the PREPROCESSED pages, exactly as the model saw them. That is the
 * honest thing to show: it is what the extraction was based on, and bounding
 * boxes are normalised against these dimensions.
 */

import { useCallback, useEffect, useState } from 'react'
import { fetchScanPage, fetchScanPages } from '../api/client'
import type { ScanPageRef } from '../types/api'

/** Zoom stops. 1 is fit-to-width, which is where a page should start. */
const ZOOMS = [1, 1.5, 2, 3] as const

function groupBySlot(pages: ScanPageRef[]): [string, ScanPageRef[]][] {
  const order = ['prescription', 'pharmacy_bill', 'lab_report', 'lab_bill']
  const bySlot = new Map<string, ScanPageRef[]>()
  for (const page of pages) {
    const list = bySlot.get(page.slot) ?? []
    list.push(page)
    bySlot.set(page.slot, list)
  }
  return [...bySlot.entries()].sort(
    (a, b) => order.indexOf(a[0]) - order.indexOf(b[0]),
  )
}

function PageView({
  scanId,
  page,
  label,
  total,
}: {
  scanId: number
  page: ScanPageRef
  label: string
  total: number
}) {
  const [src, setSrc] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const [zoom, setZoom] = useState<number>(1)

  useEffect(() => {
    let url: string | null = null
    let cancelled = false
    fetchScanPage(scanId, page.slot, page.page_no).then((got) => {
      if (cancelled) {
        // The component went away mid-fetch; release the blob rather than leak it.
        if (got) URL.revokeObjectURL(got)
        return
      }
      url = got
      setSrc(got)
      setFailed(got === null)
    })
    return () => {
      cancelled = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [scanId, page.slot, page.page_no])

  return (
    <figure className="rounded border border-ink-200 bg-surface">
      <figcaption className="flex items-center justify-between gap-3 border-b border-ink-200 px-3 py-2">
        <span className="t-small text-ink">
          {label}
          {total > 1 ? (
            <span className="t-small ml-2 text-muted">
              page {page.page_no} of {total}
            </span>
          ) : null}
        </span>
        <span className="flex items-center gap-1">
          {ZOOMS.map((level) => (
            <button
              key={level}
              type="button"
              aria-pressed={zoom === level}
              onClick={() => setZoom(level)}
              className={`t-small rounded border px-1.5 py-0.5 ${
                zoom === level
                  ? 'border-seal bg-seal font-medium text-white'
                  : 'border-ink-300 bg-surface text-muted hover:text-ink'
              }`}
            >
              {level}&times;
            </button>
          ))}
        </span>
      </figcaption>

      {/* The scroll container is what makes zoom usable: past 1x the page is
          wider than the column, and it must scroll inside its own box rather
          than pushing the review screen sideways. */}
      <div className="max-h-[32rem] overflow-auto bg-ink-50 p-2">
        {src ? (
          <img
            src={src}
            alt={`${label}, page ${page.page_no}`}
            style={{ width: `${zoom * 100}%`, maxWidth: 'none' }}
            className="block"
          />
        ) : failed ? (
          <p className="t-small px-2 py-8 text-center text-muted">
            This page was not stored with the scan.
          </p>
        ) : (
          <p className="t-small px-2 py-8 text-center text-muted">Loading…</p>
        )}
      </div>
    </figure>
  )
}

export function DocumentViewer({ scanId }: { scanId: number | null }) {
  const [pages, setPages] = useState<ScanPageRef[] | null>(null)
  const [legacyOnly, setLegacyOnly] = useState(false)
  const [open, setOpen] = useState(false)

  const load = useCallback(() => {
    if (scanId === null) return
    fetchScanPages(scanId).then((got) => {
      setPages(got?.pages ?? [])
      setLegacyOnly(got?.legacy_only ?? false)
    })
  }, [scanId])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  if (scanId === null) return null

  return (
    <section>
      <h2 className="t-micro mb-1 text-muted">Documents</h2>
      <details
        className="rounded border border-ink-200 bg-surface"
        onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}
      >
        <summary className="t-small cursor-pointer px-4 py-3 text-ink">
          Look at the uploaded pages
        </summary>
        <div className="border-t border-ink-200 px-4 py-4">
          {pages === null ? (
            <p className="t-small text-muted">Loading…</p>
          ) : pages.length === 0 ? (
            <p className="t-small text-muted">
              No pages were stored with this scan. Records made before pages were kept
              have none — which is not the same as an upload that was empty.
            </p>
          ) : (
            <>
              {legacyOnly ? (
                <p className="t-small mb-4 rounded bg-ink-100 px-3 py-2 text-muted">
                  This scan predates per-page storage, so only the first page of the
                  prescription and the pharmacy bill was kept.
                </p>
              ) : null}
              <div className="space-y-6">
                {groupBySlot(pages).map(([slot, group]) => (
                  <div key={slot} className="space-y-3">
                    {group.map((page) => (
                      <PageView
                        key={`${slot}-${page.page_no}`}
                        scanId={scanId}
                        page={page}
                        label={group[0]?.label ?? slot}
                        total={group.length}
                      />
                    ))}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </details>
    </section>
  )
}
