# JSON export for HRMS ingestion

`GET /api/scans/{id}/export.json`

Returns the complete `ReconciliationResult` **verbatim**, wrapped in an envelope
carrying the employee fields and the disclaimer.

Verbatim is the contract. An integrator diffing this against the live
`/api/reconcile` response should find the `result` object identical — nothing is
reshaped, rounded, renamed or pruned on the way out. When the API schema gains a
field, this gains it too, without a change here.

## Envelope

```json
{
  "envelope_version": "1.0",
  "generated_from": "rxconcile",
  "disclaimer": "Proof of concept. Automated document comparison only, not clinical verification and not an insurance determination. Nothing in this report approves or rejects anything. All findings require human review.",
  "reimbursement_note": "An assessment of which billed items are supported by the prescription. Coverage rules, copay tiers and policy limits appear in neither document, are not modelled, and are not inferred.",
  "scan": {
    "id": 11,
    "created_at": "2026-08-28T12:59:04.118412",
    "employee_name": "Yash",
    "employee_number": "EMP-4417",
    "prescription_filename": "p3.jpg",
    "bill_filename": "synthetic_bill_p3.png",
    "extraction_runs": 3
  },
  "result": { "...": "the full ReconciliationResult" }
}
```

`envelope_version` describes **this wrapper only**. The `result` inside it is
versioned by the API, not by the export.

## Fields an integrator will get wrong if they skim

These are the ones where an obvious-looking reading is the wrong one.

| Field | Do not assume |
| --- | --- |
| `score` | `null` under an `inconclusive` verdict. **Never coerce to 0** — zero reads as "measured, terrible" rather than "not measurable". |
| `review_summary.checks_unavailable` | A count of checks that **did not run**. Not findings, not failures, and never to be folded into a discrepancy count. Zero genuinely means every check ran. |
| `review_summary.items_needing_review` | `null` when `agreement_measured` is false. Rendering `0` would claim nothing needs review when nothing was checked. |
| `confidence` on any item | The **model's own** legibility score. It carries no reliability information and must not gate anything. Use `agreement` instead. |
| `agreement` | `null` for a single-run extraction. This — not `confidence` — is the reliability signal. |
| `prescription.items[].salt` | What was **read off the page**, usually `null`. The dictionary match is in `canonical`. |
| `canonical[].method` | `"unresolved"` means looked up and not found. Distinct from a line having no entry at all. |
| `prescription.investigations_present` | Tri-state. `false` = no tests ordered. `true` with an empty `tests` = tests ordered and **unreadable**. `null` = could not tell. Only the first is a clean result. |
| `reimbursement.*_total` | Excludes lines with no printed amount; `lines_without_amount` counts them. A total is never quietly complete. |

## Reimbursement

`result.reimbursement` sorts every billed line into exactly one of three
buckets, each traceable to the lines that built it:

| Category | Means |
| --- | --- |
| `eligible` | Matched to a prescribed line with nothing against it |
| `not_eligible` | Nothing on the prescription behind it |
| `needs_review` | A check could not be completed, or the matched prescription line carries a discrepancy |

**This is not an insurance determination.** Coverage rules, copay tiers, policy
limits and exclusions appear in none of the documents this system reads, are not
modelled, and are not inferred. An HRMS must not present these amounts as an
approval, a claim or a settlement — they answer only "which billed items are
supported by the prescription".

`needs_review` is not a rejection. In practice it is the largest bucket on real
bills, because Indian pharmacy invoices price per pack and rarely state whether
the quantity column counts packs or units, which leaves the quantity check
unable to run. See `docs/DESIGN_DECISIONS.md`.

## Authentication

The demo token from `POST /api/demo/session`, as `Authorization: Bearer <token>`.

**This is not authentication.** Credentials are hardcoded and the token is signed
with a committed secret. An employee sees only their own scans and an admin sees
all, but that is view filtering, not access control. Any real integration needs a
real identity layer first.

## Other formats

| Route | Content type |
| --- | --- |
| `GET /api/scans/{id}/export.pdf` | `application/pdf` |
| `GET /api/scans/{id}/export.xlsx` | `…spreadsheetml.sheet` |
| `GET /api/scans/{id}/export.json` | `application/json` |

All three carry the same disclaimer, and all three state what could not be
checked — including any document that was not supplied. A report outlives the
session it came from; a reader months later has no other way to know.
