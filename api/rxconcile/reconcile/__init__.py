"""Deterministic reconciliation.

HARD RULE: every match/mismatch verdict in rxconcile is produced by
deterministic Python in this package. The LLM extracts structured data from
images; it never decides whether two documents agree.

Every finding emitted from here carries a machine-readable rule code and a
severity.
"""
