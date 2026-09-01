/**
 * What an employee sees after they submit.
 *
 * Not the result. They do not review their own claim, so there are no
 * findings, no tables, no verdict and no figures here — and none of it reaches
 * the browser either, because the server sends this shape rather than the
 * reviewer's one.
 *
 * The single exception is document READABILITY. A photograph nobody could read
 * has to come back to them now, while they still have the paper in front of
 * them; otherwise the claim fails days later at review and they never learn
 * why. That is the one thing they can act on, and it is about their photo, not
 * about their medicines.
 */

import { useState } from 'react'
import { certifyScan } from '../api/client'
import { DOCUMENT_SLOTS } from '../lib/documents'
import type { DocumentReadability, EmployeeScanDetail } from '../types/api'

const STATUS_LABEL: Record<string, string> = {
  submitted: 'Submitted',
  under_review: 'Under review',
  reviewed: 'Reviewed',
}

/** How a readability state is drawn. Grey where nothing is claimed. */
const STATE_STYLE: Record<DocumentReadability['state'], { tint: string; word: string }> = {
  read: { tint: 'bg-tint-clean', word: 'Read' },
  partly_unreadable: { tint: 'bg-tint-substitution', word: 'Partly unreadable' },
  unreadable: { tint: 'bg-tint-problem', word: 'Could not read' },
  not_assessed: { tint: 'bg-tint-neutral', word: 'Not read' },
  not_supplied: { tint: 'bg-tint-neutral', word: 'Not uploaded' },
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="t-colhead text-muted">{label}</dt>
      <dd className="t-body mt-0.5 text-ink">{value || '—'}</dd>
    </div>
  )
}

function ReadabilityRow({ doc }: { doc: DocumentReadability }) {
  const style = STATE_STYLE[doc.state]
  return (
    <li className={`rounded-lg px-4 py-3 ${style.tint}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <span className="t-body font-medium text-ink">{doc.label}</span>
        <span className="t-colhead text-ink">{style.word}</span>
      </div>
      {doc.message ? <p className="t-small mt-1 text-muted">{doc.message}</p> : null}
    </li>
  )
}

export function Submitted({
  submission,
  onCertified,
  onStartAnother,
}: {
  submission: EmployeeScanDetail
  onCertified: (next: EmployeeScanDetail) => void
  onStartAnother: () => void
}) {
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState(false)

  const certified = submission.certified_by_employee
  // Only the documents that were actually uploaded. A slot nobody filled is
  // not a problem and does not need a line on this screen.
  const documents = submission.readability.filter((doc) => doc.supplied)
  const needsAttention = documents.filter(
    (doc) => doc.state === 'unreadable' || doc.state === 'partly_unreadable',
  )

  const certify = async () => {
    setSaving(true)
    setFailed(false)
    try {
      onCertified(await certifyScan(submission.id))
    } catch {
      setFailed(true)
    } finally {
      setSaving(false)
    }
  }

  const uploaded = DOCUMENT_SLOTS.map((slot) => ({
    label: slot.label,
    filename:
      slot.key === 'prescription'
        ? submission.prescription_filename
        : slot.key === 'bill'
          ? submission.bill_filename
          : '',
  })).filter((row) => row.filename)

  return (
    <div className="space-y-8">
      <section className="overflow-hidden rounded-xl bg-surface shadow-[0_1px_2px_rgba(13,18,17,0.04),0_8px_24px_-12px_rgba(13,18,17,0.18)]">
        <div className="px-6 py-7 sm:px-8">
          <p className="t-colhead text-muted">{STATUS_LABEL[submission.review_status]}</p>
          <h1 className="t-display mt-1 text-ink">Submitted for review</h1>
          <p className="t-body mt-2 max-w-2xl text-muted">
            Your claim is with the reviewer. You will not see the comparison —
            that is their job — but anything we could not read off your
            documents is below, while you can still do something about it.
          </p>

          <dl className="mt-6 grid gap-5 border-t border-ink-100 pt-6 sm:grid-cols-2 lg:grid-cols-3">
            <Field
              label="Name"
              value={[submission.first_name, submission.middle_name, submission.last_name]
                .filter(Boolean)
                .join(' ')}
            />
            <Field label="Employee number" value={submission.employee_number} />
            <Field label="Submitted" value={new Date(submission.created_at).toLocaleString()} />
            <Field label="Condition" value={submission.condition ?? ''} />
            <div className="sm:col-span-2">
              <dt className="t-colhead text-muted">Description</dt>
              <dd className="t-body mt-0.5 text-ink">{submission.description || '—'}</dd>
            </div>
          </dl>

          {uploaded.length > 0 ? (
            <div className="mt-6 border-t border-ink-100 pt-6">
              <p className="t-colhead text-muted">Documents received</p>
              <ul className="mt-2 space-y-1">
                {uploaded.map((row) => (
                  <li key={row.label} className="t-small text-ink">
                    <span className="text-muted">{row.label} — </span>
                    {row.filename}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      </section>

      <section>
        <h2 className="t-title text-ink">What we could read</h2>
        <p className="t-small mt-1 max-w-2xl text-muted">
          {needsAttention.length > 0
            ? 'Please replace the documents marked below. A claim reviewed from an ' +
              'unreadable photo has to come back to you anyway.'
            : 'Nothing here needs a better photograph.'}
        </p>
        <ul className="mt-3 space-y-2">
          {documents.map((doc) => (
            <ReadabilityRow key={doc.slot} doc={doc} />
          ))}
        </ul>
      </section>

      <section className="rounded-xl bg-ink-50 px-6 py-5">
        {certified ? (
          <p className="t-body text-ink">
            <span className="font-semibold">Certified.</span> You confirmed these
            documents are genuine and relate to expenses you have incurred
            {submission.certified_at
              ? ` on ${new Date(submission.certified_at).toLocaleString()}`
              : ''}
            .
          </p>
        ) : (
          <>
            <label className="t-body flex cursor-pointer items-start gap-3 text-ink">
              <input
                type="checkbox"
                checked={false}
                disabled={saving}
                onChange={certify}
                className="mt-1 h-4 w-4 accent-[color:var(--color-seal)]"
              />
              I confirm the documents I have uploaded are genuine and relate to
              expenses I have incurred.
            </label>
            <p className="t-small mt-2 text-muted">
              Your submission is not complete until this is ticked.
            </p>
            {failed ? (
              <p className="t-small mt-2 text-flag">
                That could not be saved. Please try again.
              </p>
            ) : null}
          </>
        )}
      </section>

      <div className="border-t border-ink-200 pt-6">
        <button
          type="button"
          onClick={onStartAnother}
          className="t-small rounded border border-ink-300 px-4 py-2 text-ink hover:bg-paper"
        >
          Submit another claim
        </button>
      </div>
    </div>
  )
}
