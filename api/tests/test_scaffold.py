"""Scaffold sanity checks. No application logic exists yet."""

from __future__ import annotations

import rxconcile
from rxconcile import reconcile


def test_package_imports() -> None:
    assert rxconcile.__version__ == "0.1.0"


def test_reconcile_subpackage_exists() -> None:
    assert reconcile.__doc__ is not None
