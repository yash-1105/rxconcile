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
        # Verbatim. Not reshaped, not rounded, not pruned.
        "result": context.result.model_dump(mode="json"),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
