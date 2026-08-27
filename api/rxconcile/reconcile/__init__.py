"""Deterministic reconciliation.

HARD RULE: every match/mismatch verdict in rxconcile is produced by
deterministic Python in this package. The LLM extracts structured data from
images; it never decides whether two documents agree.

Every finding emitted from here carries a machine-readable rule code and a
severity.
"""

from rxconcile.reconcile.engine import (
    PAIR_THRESHOLD,
    compute_score,
    decide_verdict,
    is_inconclusive,
    reconcile,
    similarity,
)

__all__ = [
    "PAIR_THRESHOLD",
    "compute_score",
    "decide_verdict",
    "is_inconclusive",
    "reconcile",
    "similarity",
]
