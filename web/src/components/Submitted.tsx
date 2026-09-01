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
import type {
  DocumentReadability,
  EmployeeScanDetail,
  ExtractedContent,
} from '../types/api'

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


/**
 * What was read off the documents.
 *
 * Every row here looks like every other row. There is no tint, no mark and no
 * status, because there is no comparison in this data — a medicine on the bill
 * that was never prescribed is drawn exactly like one that was, and the
 * response carries nothing that could tell them apart.
 */
function money(amount: string | null, currency: string): string {
  if (amount === null) return '—'
  const value = Number(amount)
  return `${currency} ${
    Number.isFinite(value)
      ? value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      : amount
  }`
}

function Header({ pairs }: { pairs: Array<[string, string | null]> }) {
  const shown = pairs.filter(([, value]) => value)
  if (shown.length === 0) return null
  return (
    <dl className="flex flex-wrap gap-x-8 gap-y-3">
      {shown.map(([label, value]) => (
        <div key={label}>
          <dt className="t-colhead text-muted">{label}</dt>
          <dd className="t-small mt-0.5 text-ink">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

function Totals({
  rows,
  currency,
}: {
  rows: Array<[string, string | null]>
  currency: string
}) {
  const shown = rows.filter(([, value]) => value !== null)
  if (shown.length === 0) return null
  return (
    <dl className="mt-3 flex flex-wrap justify-end gap-x-8 gap-y-2 border-t border-ink-100 pt-3">
      {shown.map(([label, value]) => (
        <div key={label} className="text-right">
          <dt className="t-colhead text-muted">{label}</dt>
          <dd className="t-data mt-0.5 text-ink">{money(value, currency)}</dd>
        </div>
      ))}
    </dl>
  )
}

/** A plain table. No row tint anywhere in this file, deliberately. */
function Rows({ heads, children }: { heads: string[]; children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b border-ink-200 text-left">
            {heads.map((head) => (
              <th key={head} className="t-colhead px-3 pb-2">
                {head}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  )
}

function Cell({ value, mono = false }: { value: string | null; mono?: boolean }) {
  return (
    <td className={`${mono ? 't-data' : 't-small'} px-3 py-3 align-top text-ink`}>
      {value ?? <span className="text-unknown">—</span>}
    </td>
  )
}

function DocumentPanel({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <section className="rounded-xl bg-surface px-5 py-5 sm:px-6">
      <h3 className="t-title text-ink">{title}</h3>
      <div className="mt-4 space-y-4">{children}</div>
    </section>
  )
}

function ExtractedSections({ content }: { content: ExtractedContent }) {
  const { prescription, pharmacy_bill: bill, lab_bill: lab } = content
  return (
    <div className="space-y-4">
      <DocumentPanel title="From your prescription">
        <Header
          pairs={[
            ['Prescriber', prescription.prescriber],
            ['Clinic', prescription.clinic],
            ['Date', prescription.date],
            ['Patient', prescription.patient_name],
            ['Age', prescription.patient_age],
            ['Sex', prescription.patient_sex],
          ]}
        />
        {prescription.medicines.length > 0 ? (
          <Rows heads={['Medicine', 'Strength', 'Form', 'Frequency', 'Duration']}>
            {prescription.medicines.map((line, index) => (
              <tr key={`${line.raw_text}-${index}`} className="border-b border-ink-100">
                <Cell value={line.name} mono />
                <Cell value={line.strength} mono />
                <Cell value={line.form} />
                <Cell value={line.frequency} mono />
                <Cell value={line.duration} />
              </tr>
            ))}
          </Rows>
        ) : (
          <p className="t-small text-muted">No medicine line was read off this page.</p>
        )}
        <div>
          <p className="t-colhead text-muted">Investigations ordered</p>
          {prescription.investigations.length > 0 ? (
            <ul className="mt-1 space-y-0.5">
              {prescription.investigations.map((name, index) => (
                <li key={`${name}-${index}`} className="t-small text-ink">
                  {name}
                </li>
              ))}
            </ul>
          ) : (
            <p className="t-small mt-1 text-muted">None was read off this page.</p>
          )}
        </div>
      </DocumentPanel>

      <DocumentPanel title="From your pharmacy bill">
        <Header
          pairs={[
            ['Pharmacy', bill.name],
            ['Bill number', bill.bill_no],
            ['Date', bill.bill_date],
          ]}
        />
        {bill.lines.length > 0 ? (
          <>
            <Rows heads={['Item', 'Batch', 'Expiry', 'Pack', 'Qty', 'Rate', 'Amount']}>
              {bill.lines.map((line, index) => (
                <tr key={`${line.raw_text}-${index}`} className="border-b border-ink-100">
                  <Cell value={line.item} mono />
                  <Cell value={line.batch} mono />
                  <Cell value={line.expiry} mono />
                  <Cell value={line.pack} mono />
                  <Cell value={line.quantity} mono />
                  <Cell value={line.rate ? money(line.rate, bill.currency) : null} mono />
                  <Cell value={line.amount ? money(line.amount, bill.currency) : null} mono />
                </tr>
              ))}
            </Rows>
            <Totals
              rows={[
                ['Subtotal', bill.subtotal],
                ['Tax', bill.tax],
                ['Total', bill.grand_total],
              ]}
              currency={bill.currency}
            />
          </>
        ) : (
          <p className="t-small text-muted">No line was read off this page.</p>
        )}
      </DocumentPanel>

      {lab ? (
        <DocumentPanel title="From your lab bill">
          <Header
            pairs={[
              ['Laboratory', lab.name],
              ['Bill number', lab.bill_no],
              ['Date', lab.bill_date],
            ]}
          />
          {lab.tests.length > 0 ? (
            <>
              <Rows heads={['Test', 'Amount']}>
                {lab.tests.map((line, index) => (
                  <tr key={`${line.raw_text}-${index}`} className="border-b border-ink-100">
                    <Cell value={line.test} mono />
                    <Cell value={line.amount ? money(line.amount, lab.currency) : null} mono />
                  </tr>
                ))}
              </Rows>
              <Totals
                rows={[
                  ['Subtotal', lab.subtotal],
                  ['Tax', lab.tax],
                  ['Total', lab.grand_total],
                ]}
                currency={lab.currency}
              />
            </>
          ) : (
            <p className="t-small text-muted">No test line was read off this page.</p>
          )}
        </DocumentPanel>
      ) : null}
    </div>
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

      {submission.content?.billed_total ? (
        <section className="rounded-xl bg-ink-50 px-6 py-5">
          <p className="t-colhead text-muted">Total on your bills</p>
          <p className="t-hero mt-1 text-ink">
            {money(submission.content.billed_total, submission.content.currency)}
          </p>
          {/* Deliberately not a claimable, eligible or supported figure. Those
              come from a comparison the submitter does not see, they move when
              a reviewer rejects a line, and printing one would promise an
              amount nobody has agreed to. */}
          <p className="t-small mt-2 max-w-2xl text-muted">
            This is the amount on the documents you uploaded. What is reimbursable is
            decided at review.
          </p>
        </section>
      ) : submission.content ? (
        <section className="rounded-xl bg-ink-50 px-6 py-5">
          <p className="t-colhead text-muted">Total on your bills</p>
          <p className="t-body mt-1 text-ink">
            Your documents do not print a total we can add up without leaving a line out,
            so none is shown. The amounts we did read are listed below.
          </p>
        </section>
      ) : null}

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

      {submission.content ? (
        <section>
          <h2 className="t-title text-ink">What we read off your documents</h2>
          <p className="t-small mt-1 max-w-2xl text-muted">
            Everything below came off the pages you uploaded. Check it against the paper —
            if something was misread, a clearer photo will fix it. Nothing here compares
            one document to another; that is done at review.
          </p>
          <div className="mt-3">
            <ExtractedSections content={submission.content} />
          </div>
        </section>
      ) : null}

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
