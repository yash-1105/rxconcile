"""The export's row model against the screen's.

`rxconcile.export.rows` mirrors `web/src/lib/rows.ts` and `rowStatus.ts` so a
report is a record of the screen rather than a second opinion about it. These
assert the shapes and states both sides must agree on; the row-key contract
itself is asserted in test_export_decisions.py and web/src/lib/rowKeys.test.ts.
"""

from __future__ import annotations

from decimal import Decimal

from rxconcile.export.rows import (
    STATUS_LABEL,
    TINT,
    counts,
    medicine_rows,
    status_of,
)
from rxconcile.export.rows import test_rows as lab_rows
from rxconcile.models import (
    BilledItem,
    BilledTest,
    Finding,
    PharmacyBill,
    PrescribedItem,
    PrescribedTest,
    Prescription,
    ReconciliationResult,
)
from rxconcile.reconcile import engine


def _f(code: str, severity: str, **refs: str | None) -> Finding:
    return Finding(
        rule_code=code, severity=severity, message=code,  # type: ignore[arg-type]
        prescribed_ref=refs.get("rx"), billed_ref=refs.get("bill"), detail={},
    )


class TestStatusPrecedence:
    """critical > warning > non-medicine > paired. Mirrors `statusFrom`."""

    def test_a_critical_leads(self) -> None:
        found = [_f("FORM_MISMATCH", "warning"), _f("STRENGTH_MISMATCH", "critical")]
        assert status_of(found, paired=True)[0] == "problem"

    def test_a_confirmed_non_medicine_is_out_of_scope(self) -> None:
        assert status_of([_f("NON_MEDICINE_ITEM", "info")], paired=False)[0] == "out-of-scope"

    def test_a_non_medicine_never_hides_a_real_finding(self) -> None:
        found = [_f("NON_MEDICINE_ITEM", "info"), _f("EXPIRED_ITEM", "critical")]
        assert status_of(found, paired=False)[0] == "problem"

    def test_a_clean_pair_with_a_brand_swap_is_a_substitution(self) -> None:
        assert status_of([_f("BRAND_SUBSTITUTION", "info")], paired=True)[0] == "substitution"

    def test_a_pair_with_nothing_against_it_matches(self) -> None:
        assert status_of([], paired=True)[0] == "clean"

    def test_an_unpaired_line_with_nothing_against_it_is_unchecked(self) -> None:
        assert status_of([], paired=False)[0] == "unchecked"

    def test_an_unrunnable_check_marks_the_row_without_downgrading_it(self) -> None:
        """The defect the precedence exists to prevent."""
        state, partial = status_of(
            [_f("BRAND_SUBSTITUTION", "info"), _f("QUANTITY_AMBIGUOUS", "info")], paired=True
        )
        assert state == "substitution"
        assert partial is True


def test_every_state_has_a_word_and_a_tint() -> None:
    """A tint alone cannot carry a status: the report must print in greyscale."""
    assert set(STATUS_LABEL) == set(TINT)
    assert all(word.strip() for word in STATUS_LABEL.values())


def test_counts_leave_out_of_scope_and_unchecked_out_of_both_buckets() -> None:
    """Mirrors `countRows`. Neither is a match, and neither is a problem."""
    tally = counts(
        ["clean", "substitution", "problem", "warning", "unchecked", "out-of-scope"]
    )
    assert tally.matched == 2
    assert tally.problems == 2


def test_a_billed_only_finding_reaches_its_matched_row() -> None:
    """EXPIRED_ITEM names the billed line alone and used to be dropped."""
    prescription = Prescription(
        overall_legibility=0.9,
        items=[PrescribedItem(item_id="rx-01", raw_text="Dolo", drug_name="Dolo",
                              confidence=0.9)],
    )
    bill = PharmacyBill(
        currency="INR",
        items=[BilledItem(item_id="bill-01", raw_text="DOLO", drug_name="Dolo",
                          quantity=1.0, line_total=Decimal("30"), confidence=0.9)],
    )
    result = engine.reconcile(prescription, bill, processing_ms=0)
    result = ReconciliationResult.model_validate(
        {**result.model_dump(), "findings": [
            *[f.model_dump() for f in result.findings],
            _f("EXPIRED_ITEM", "critical", bill="bill-01").model_dump(),
        ]}
    )
    row = medicine_rows(result)[0]
    assert "EXPIRED_ITEM" in [f.rule_code for f in row.findings]
    assert row.state == "problem"


def test_panel_components_are_attributed_to_the_panel_that_covers_them() -> None:
    """Stated by the engine via MatchedPair.covers, never guessed."""
    prescription = Prescription(
        overall_legibility=0.9, investigations_present=True,
        tests=[PrescribedTest(item_id="t-01", raw_text="Lipid Profile",
                              test_name="Lipid Profile", confidence=0.9)],
    )
    names = ["Lipid Profile - Total Cholesterol", "Lipid Profile - HDL Cholesterol",
             "Lipid Profile - LDL Cholesterol", "Lipid Profile - Triglycerides"]
    bill = PharmacyBill(
        currency="INR",
        tests=[BilledTest(item_id=f"b-{i:02d}", raw_text=n, test_name=n,
                          line_total=Decimal("200"), confidence=0.9)
               for i, n in enumerate(names, start=1)],
    )
    rows = lab_rows(engine.reconcile(prescription, bill, processing_ms=0))
    parent = next(r for r in rows if r.prescribed is not None)
    children = [r for r in rows if r.covered_by is not None]
    assert parent.covers_count == 4
    assert len(children) == 3, "the primary is the parent; the other three are children"
    assert {c.covered_by for c in children} == {"Lipid Profile"}
    # Every billed line is accounted for. A table that drops one is worse than none.
    assert len([r for r in rows if r.billed is not None]) == 4
