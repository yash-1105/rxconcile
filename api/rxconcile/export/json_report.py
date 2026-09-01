"""JSON export for HRMS ingestion.

The full ReconciliationResult verbatim, wrapped in an envelope carrying the
employee fields. Verbatim matters: an HRMS integrator
diffing this against the live API response should find the result identical, so
nothing here reshapes, rounds or prunes it.

See docs/HRMS_EXPORT.md.
"""

from __future__ import annotations

import json
from decimal import Decimal

from rxconcile.export.common import REIMBURSEMENT_NOTE, ExportContext, category_totals
from rxconcile.export.rows import counts, medicine_rows, test_rows

#: Bump when the envelope changes shape. The `result` object inside it is
#: versioned by the API, not by this.
ENVELOPE_VERSION = "1.0"


def _money(amount: Decimal | None) -> str | None:
    """Amounts as strings, never floats. A rupee is not a binary fraction."""
    return None if amount is None else str(amount)


def build_json(context: ExportContext) -> bytes:
    med = counts([r.state for r in medicine_rows(context.result)])
    tests = counts([r.state for r in test_rows(context.result)])
    balance: Decimal | None = None
    if context.annual_amount is not None:
        drawn = (context.used_amount or Decimal("0")) + (context.claimed_amount or Decimal("0"))
        balance = max(Decimal("0"), context.annual_amount - drawn)
    payload = {
        "envelope_version": ENVELOPE_VERSION,
        "generated_from": "rxconcile",
        "reimbursement_note": REIMBURSEMENT_NOTE,
        "scan": {
            "id": context.scan_id,
            "created_at": context.created_at.isoformat() if context.created_at else None,
            "employee_name": context.employee_name,
            "employee_number": context.employee_number,
            "prescription_filename": context.prescription_filename,
            "bill_filename": context.bill_filename,
            "extraction_runs": context.extraction_runs,
        },
        # What a reviewer decided, keyed by the same row identifiers the screen
        # and the other two reports use. `decision` is one of accept, reject or
        # unset; unset means nobody has ruled on that line, which is NOT the
        # same as rejecting it and must not be read as an approval either.
        "review": {
            "claimed_amount": (
                str(context.claimed_amount) if context.claimed_amount is not None else None
            ),
            "currency": context.result.reimbursement.currency,
            "decisions": context.decisions,
            "note": (
                "claimed_amount is the total of the lines marked accept that are claimable: "
                "a medicine matched to a prescription line, or a lab test matched to an "
                "ordered test. Lines not on the prescription and lines that are not "
                "medicines are never claimable. This is a recorded judgement, not a "
                "settlement."
            ),
        },
        # The same figures the dashboard and the other two reports lead with,
        # so an integrator does not have to re-derive them. `needs_review` is
        # deliberately absent: it is not a category anybody acts on, and each of
        # those lines is reported as what it actually is. The verbatim result
        # below still carries the engine's own buckets untouched.
        "summary": {
            "verdict": context.result.verdict,
            "counts": {
                "medicines_matched": med.matched,
                "medicines_with_problems": med.problems,
                "lab_tests_matched": tests.matched,
                "lab_tests_with_problems": tests.problems,
            },
            "allowance": {
                "year": context.allowance_year or None,
                "annual_amount": _money(context.annual_amount),
                "used_excluding_this_claim": _money(context.used_amount),
                "this_claim": _money(context.claimed_amount),
                "balance_remaining": _money(balance),
            },
            "reimbursement": {
                key: {"total": str(total), "lines": count}
                for key, total, count in category_totals(context.result)
            },
            "currency": context.result.reimbursement.currency,
        },
        # Verbatim. Not reshaped, not rounded, not pruned.
        "result": context.result.model_dump(mode="json"),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
