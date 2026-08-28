"""Shared finding constructors.

Lifted out of ``engine`` so the medicine rules and the lab-test rules build
findings the same way rather than growing two dialects of the same object.
"""

from __future__ import annotations

from typing import Any

from rxconcile.models import Finding, Severity
from rxconcile.models.schema import CHECK_UNAVAILABLE_CODE


def finding(
    rule_code: str,
    severity: Severity,
    message: str,
    *,
    prescribed_ref: str | None = None,
    billed_ref: str | None = None,
    detail: dict[str, Any] | None = None,
) -> Finding:
    return Finding(
        rule_code=rule_code,
        severity=severity,
        message=message,
        prescribed_ref=prescribed_ref,
        billed_ref=billed_ref,
        detail=detail or {},
    )


def unavailable(
    check: str,
    missing: list[str],
    *,
    prescribed_ref: str | None = None,
    billed_ref: str | None = None,
    note: str = "",
) -> Finding:
    """Record that a named check could not run.

    The engine previously had no way to say this: a rule either fired or was
    absent, and absence rendered identically to "checked, nothing found". Every
    silent skip in docs/NULL_MATRIX.md was that same gap.
    """
    joined = ", ".join(missing)
    return finding(
        CHECK_UNAVAILABLE_CODE, "info",
        f"The {check} check could not run: {joined} {'is' if len(missing) == 1 else 'are'} "
        f"not present on the document{'.' if not note else '. ' + note}",
        prescribed_ref=prescribed_ref,
        billed_ref=billed_ref,
        detail={"check": check, "missing": missing, "note": note or None},
    )
