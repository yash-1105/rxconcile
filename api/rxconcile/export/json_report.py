"""JSON export for HRMS ingestion.

The full ReconciliationResult verbatim, wrapped in an envelope carrying the
employee fields and the disclaimer. Verbatim matters: an HRMS integrator
diffing this against the live API response should find the result identical, so
nothing here reshapes, rounds or prunes it.

See docs/HRMS_EXPORT.md.
"""

from __future__ import annotations

import json

from rxconcile.export.common import DISCLAIMER, REIMBURSEMENT_NOTE, ExportContext

#: Bump when the envelope changes shape. The `result` object inside it is
#: versioned by the API, not by this.
ENVELOPE_VERSION = "1.0"


def build_json(context: ExportContext) -> bytes:
    payload = {
        "envelope_version": ENVELOPE_VERSION,
        "generated_from": "rxconcile",
        "disclaimer": DISCLAIMER,
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
        # Verbatim. Not reshaped, not rounded, not pruned.
        "result": context.result.model_dump(mode="json"),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
