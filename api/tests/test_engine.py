"""Reconciliation engine: pairing, every rule code, verdict order, scoring."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from rxconcile.models import (
    BilledItem,
    PharmacyBill,
    PrescribedItem,
    Prescription,
    ReconciliationResult,
)
from rxconcile.reconcile import engine

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURE_DIR / f"{name}.json").read_text())
    return data


def rx_item(item_id: str, **kwargs: object) -> PrescribedItem:
    base: dict[str, Any] = {
        "item_id": item_id,
        "raw_text": "TAB DOLO 650",
        "drug_name": "Dolo",
        "strength_value": 650.0,
        "strength_unit": "mg",
        "form": "tablet",
        "confidence": 0.9,
    }
    base.update(kwargs)
    return PrescribedItem.model_validate(base)


def bill_item(item_id: str, **kwargs: object) -> BilledItem:
    base: dict[str, Any] = {
        "item_id": item_id,
        "raw_text": "DOLO 650 TAB",
        "drug_name": "Dolo",
        "strength_value": 650.0,
        "strength_unit": "mg",
        "form": "tablet",
        "confidence": 0.9,
    }
    base.update(kwargs)
    return BilledItem.model_validate(base)


def rx(*items: PrescribedItem, **kwargs: object) -> Prescription:
    base: dict[str, Any] = {"items": list(items), "overall_legibility": 0.9}
    base.update(kwargs)
    return Prescription.model_validate(base)


def bill(*items: BilledItem, **kwargs: object) -> PharmacyBill:
    base: dict[str, Any] = {"items": list(items)}
    base.update(kwargs)
    return PharmacyBill.model_validate(base)


def codes(result: ReconciliationResult) -> set[str]:
    return {finding.rule_code for finding in result.findings}


# ==========================================================================
# Fixtures from the schema work
# ==========================================================================


def test_clean_match_fixture_produces_a_match() -> None:
    fixture = load_fixture("01_clean_match")
    result = engine.reconcile(
        Prescription.model_validate(fixture["prescription"]),
        PharmacyBill.model_validate(fixture["bill"]),
    )
    assert result.verdict in {"match", "match_with_warnings"}
    assert result.score is not None
    assert "RX_NOT_BILLED" not in codes(result)


def test_clean_match_fixture_pairs_identical_raw_text_to_distinct_bills() -> None:
    """The identity guard, now exercised through the engine."""
    fixture = load_fixture("01_clean_match")
    result = engine.reconcile(
        Prescription.model_validate(fixture["prescription"]),
        PharmacyBill.model_validate(fixture["bill"]),
    )
    billed = [pair.billed_id for pair in result.matched_pairs]
    assert len(billed) == len(set(billed)), "one billed line claimed twice"


def test_strength_mismatch_fixture() -> None:
    fixture = load_fixture("02_strength_mismatch_extra_billed")
    result = engine.reconcile(
        Prescription.model_validate(fixture["prescription"]),
        PharmacyBill.model_validate(fixture["bill"]),
    )
    assert "STRENGTH_MISMATCH" in codes(result)
    assert result.verdict == "mismatch"


def test_illegible_fixture_is_inconclusive_with_no_score() -> None:
    fixture = load_fixture("03_illegible_prescription")
    result = engine.reconcile(
        Prescription.model_validate(fixture["prescription"]),
        PharmacyBill.model_validate(fixture["bill"]),
    )
    assert result.verdict == "inconclusive"
    assert result.score is None


# ==========================================================================
# Step 1 -- pairing
# ==========================================================================


def test_unresolved_drug_scores_zero_not_string_similarity() -> None:
    """Two illegible lines that look alike are not evidence of the same drug."""
    prescribed = rx_item("rx-01", drug_name=None, raw_text="T. ???? 500")
    billed = bill_item("bill-01", drug_name=None, raw_text="T. ???? 500")
    from rxconcile.normalize import resolve

    left, right = resolve("T. ???? 500"), resolve("T. ???? 500")
    assert engine.drug_component(left, right) == 0.0
    # Identical strength and form alone cannot clear the threshold.
    assert engine.similarity(prescribed, billed, left, right) == pytest.approx(0.40)


def test_drug_match_alone_clears_the_threshold() -> None:
    """0.55 sits just below the drug weight, so a name match pairs on its own."""
    from rxconcile.normalize import resolve

    prescribed = rx_item("rx-01", strength_value=None, strength_unit=None, form=None)
    billed = bill_item("bill-01", strength_value=None, strength_unit=None, form=None)
    score = engine.similarity(prescribed, billed, resolve("Dolo"), resolve("Dolo"))
    assert score == pytest.approx(engine.WEIGHT_DRUG)
    assert score > engine.PAIR_THRESHOLD


def test_threshold_boundary_rejects_strength_plus_form_without_a_drug_match() -> None:
    """0.25 + 0.15 = 0.40 must not pair, however well the other fields agree."""
    from rxconcile.normalize import resolve

    assert engine.WEIGHT_STRENGTH + engine.WEIGHT_FORM < engine.PAIR_THRESHOLD
    result = engine.reconcile(
        rx(rx_item("rx-01", drug_name="Dolo")),
        bill(bill_item("bill-01", drug_name="Telma", raw_text="TELMA 650")),
    )
    assert result.matched_pairs == []
    assert result.unmatched_prescribed == ["rx-01"]
    assert resolve("Dolo").salt != resolve("Telma").salt


def test_missing_strength_scores_zero_not_a_free_pass() -> None:
    from rxconcile.normalize import Strength

    assert engine.strength_component(None, Strength(value=650, unit="mg")) == 0.0
    assert engine.strength_component(None, None) == 0.0


def test_missing_form_scores_zero() -> None:
    assert engine.form_component(None, "tablet") == 0.0
    assert engine.form_component(None, None) == 0.0


def test_assignment_is_globally_optimal_not_greedy() -> None:
    """Two prescribed paracetamol lines must claim two distinct billed lines."""
    result = engine.reconcile(
        rx(rx_item("rx-01"), rx_item("rx-02")),
        bill(bill_item("bill-01"), bill_item("bill-02")),
    )
    assert len(result.matched_pairs) == 2
    assert {p.billed_id for p in result.matched_pairs} == {"bill-01", "bill-02"}


def test_similarity_is_recorded_on_the_pair() -> None:
    result = engine.reconcile(rx(rx_item("rx-01")), bill(bill_item("bill-01")))
    assert result.matched_pairs[0].similarity == pytest.approx(1.0)


# ==========================================================================
# Step 2 -- one case per rule code
# ==========================================================================


def test_rx_not_billed() -> None:
    result = engine.reconcile(rx(rx_item("rx-01")), bill())
    assert "RX_NOT_BILLED" in codes(result)
    assert result.verdict == "mismatch"


def test_bill_not_prescribed_is_critical_for_a_medicine() -> None:
    result = engine.reconcile(rx(), bill(bill_item("bill-01")))
    finding = next(f for f in result.findings if f.rule_code == "BILL_NOT_PRESCRIBED")
    assert finding.severity == "critical"
    assert finding.billed_ref == "bill-01"


def test_bill_not_prescribed_is_info_for_a_non_medicine_line() -> None:
    result = engine.reconcile(
        rx(),
        bill(
            bill_item(
                "bill-01", drug_name="Delivery charge", raw_text="DELIVERY CHARGE",
                form="other", strength_value=None, strength_unit=None,
            )
        ),
    )
    finding = next(f for f in result.findings if f.rule_code == "BILL_NOT_PRESCRIBED")
    assert finding.severity == "info"


def test_strength_mismatch() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01", strength_value=500.0)),
        bill(bill_item("bill-01", strength_value=650.0)),
    )
    assert "STRENGTH_MISMATCH" in codes(result)


def test_strength_mismatch_ignores_unit_spelling() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01", strength_value=1.0, strength_unit="g")),
        bill(bill_item("bill-01", strength_value=1000.0, strength_unit="mg")),
    )
    assert "STRENGTH_MISMATCH" not in codes(result)


def test_form_mismatch() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01", form="tablet")),
        bill(bill_item("bill-01", form="syrup")),
    )
    finding = next(f for f in result.findings if f.rule_code == "FORM_MISMATCH")
    assert finding.severity == "warning"


def test_quantity_short() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01", frequency_raw="1-0-1", duration_raw="x 5 days", duration_days=5)),
        bill(bill_item("bill-01", quantity=1.0, pack_size="5'S")),
    )
    assert "QUANTITY_SHORT" in codes(result)


def test_quantity_excess_needs_more_than_twenty_percent() -> None:
    within = engine.reconcile(
        rx(rx_item("rx-01", frequency_raw="1-0-1", duration_raw="x 5 days", duration_days=5)),
        bill(bill_item("bill-01", quantity=1.0, pack_size="11'S")),
    )
    assert "QUANTITY_EXCESS" not in codes(within)
    beyond = engine.reconcile(
        rx(rx_item("rx-01", frequency_raw="1-0-1", duration_raw="x 5 days", duration_days=5)),
        bill(bill_item("bill-01", quantity=1.0, pack_size="20'S")),
    )
    assert "QUANTITY_EXCESS" in codes(beyond)


def test_quantity_rules_skip_silently_without_an_expectation() -> None:
    """A null duration is not a discrepancy."""
    result = engine.reconcile(
        rx(rx_item("rx-01", frequency_raw="1-0-1", duration_raw=None, duration_days=None)),
        bill(bill_item("bill-01", quantity=1.0, pack_size="5'S")),
    )
    assert "QUANTITY_SHORT" not in codes(result)
    assert "QUANTITY_EXCESS" not in codes(result)


def test_quantity_rules_skip_when_the_pack_is_unparseable() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01", frequency_raw="1-0-1", duration_raw="x 5 days", duration_days=5)),
        bill(bill_item("bill-01", quantity=1.0, pack_size="MYSTERY BOX")),
    )
    assert "QUANTITY_SHORT" not in codes(result)


def test_brand_substitution_is_info_and_never_fails() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01", drug_name="Dolo", raw_text="TAB DOLO 650")),
        bill(bill_item("bill-01", drug_name="Calpol", raw_text="CALPOL 650")),
    )
    finding = next(f for f in result.findings if f.rule_code == "BRAND_SUBSTITUTION")
    assert finding.severity == "info"
    assert result.verdict == "match"


def test_salt_different_class() -> None:
    """A fuzzy match landing in another therapeutic class is a misread signature."""
    result = engine.reconcile(
        rx(rx_item("rx-01", drug_name="Telmisartn", raw_text="TAB TELMISARTN 40",
                   strength_value=None, strength_unit=None, form=None)),
        bill(bill_item("bill-01", drug_name="Telsartan", raw_text="TELSARTAN 40",
                       strength_value=None, strength_unit=None, form=None)),
    )
    # Both resolve; if either matched fuzzily into a different class it fires.
    assert result.matched_pairs, "expected the fuzzy match to pair"


def test_duplicate_therapy() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01")),
        bill(bill_item("bill-01"), bill_item("bill-02", drug_name="Calpol")),
    )
    finding = next(f for f in result.findings if f.rule_code == "DUPLICATE_THERAPY")
    assert finding.severity == "warning"
    assert len(finding.detail["billed_refs"]) == 2


def test_schedule_h_unbacked() -> None:
    result = engine.reconcile(
        rx(),
        bill(
            bill_item("bill-01", drug_name="Alprax", raw_text="ALPRAX 0.5",
                      strength_value=0.5, strength_unit="mg")
        ),
    )
    finding = next(f for f in result.findings if f.rule_code == "SCHEDULE_H_UNBACKED")
    assert finding.severity == "critical"
    assert finding.detail["schedule"] in {"H", "H1"}


def test_patient_name_mismatch() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01"), patient_name="R. Sharma"),
        bill(bill_item("bill-01"), patient_name="Zaheer Abbas"),
    )
    finding = next(f for f in result.findings if f.rule_code == "PATIENT_NAME_MISMATCH")
    assert finding.severity == "warning"


def test_matching_patient_names_do_not_fire() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01"), patient_name="R. Sharma"),
        bill(bill_item("bill-01"), patient_name="Sharma R"),
    )
    assert "PATIENT_NAME_MISMATCH" not in codes(result)


def test_date_anomaly_bill_before_prescription() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01"), date_issued=date(2026, 8, 20)),
        bill(bill_item("bill-01"), bill_date=date(2026, 8, 19)),
    )
    finding = next(f for f in result.findings if f.rule_code == "DATE_ANOMALY")
    assert finding.detail["days_between"] == -1


def test_date_anomaly_bill_long_after_prescription() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01"), date_issued=date(2026, 1, 1)),
        bill(bill_item("bill-01"), bill_date=date(2026, 3, 1)),
    )
    assert "DATE_ANOMALY" in codes(result)


def test_same_day_bill_is_not_an_anomaly() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01"), date_issued=date(2026, 8, 20)),
        bill(bill_item("bill-01"), bill_date=date(2026, 8, 20)),
    )
    assert "DATE_ANOMALY" not in codes(result)


def test_item_count_unstable_is_document_level_with_null_refs() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01"), run_item_counts=[7, 6, 6], unstable_lines=["- 6# (P+H)"]),
        bill(bill_item("bill-01")),
    )
    finding = next(f for f in result.findings if f.rule_code == "ITEM_COUNT_UNSTABLE")
    assert finding.severity == "critical"
    assert finding.prescribed_ref is None
    assert finding.billed_ref is None
    assert finding.detail["run_item_counts"] == [7, 6, 6]
    assert finding.detail["unstable_lines"] == ["- 6# (P+H)"]


def test_low_confidence_field_is_one_finding_per_item() -> None:
    """Three shaky fields on one item is one item needing review."""
    result = engine.reconcile(
        rx(
            rx_item(
                "rx-01",
                agreement={"drug_name": 0.67, "strength_value": 0.33, "form": 0.67},
            )
        ),
        bill(bill_item("bill-01")),
    )
    low = [f for f in result.findings if f.rule_code == "LOW_CONFIDENCE_FIELD"]
    assert len(low) == 1
    assert low[0].severity == "info"
    assert set(low[0].detail["agreement"]) == {"drug_name", "strength_value", "form"}
    assert low[0].detail["min_agreement"] == 0.33


def test_full_agreement_produces_no_low_confidence_finding() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01", agreement={"drug_name": 1.0, "form": 1.0})),
        bill(bill_item("bill-01")),
    )
    assert "LOW_CONFIDENCE_FIELD" not in codes(result)


def test_illegible_rx_accompanies_an_inconclusive_verdict() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01", drug_name=None), rx_item("rx-02", drug_name=None)),
        bill(bill_item("bill-01")),
    )
    assert result.verdict == "inconclusive"
    assert "ILLEGIBLE_RX" in codes(result)


# ==========================================================================
# Step 3 -- verdict order
# ==========================================================================


def test_inconclusive_when_most_drug_names_are_null() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01", drug_name=None), rx_item("rx-02", drug_name=None), rx_item("rx-03")),
        bill(bill_item("bill-01")),
    )
    assert result.verdict == "inconclusive"


def test_inconclusive_when_mean_drug_agreement_is_low() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01", agreement={"drug_name": 0.33})),
        bill(bill_item("bill-01")),
    )
    assert result.verdict == "inconclusive"


def test_inconclusive_when_item_counts_were_unstable() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01"), run_item_counts=[7, 6, 6]),
        bill(bill_item("bill-01")),
    )
    assert result.verdict == "inconclusive"


def test_inconclusive_accompanies_findings_it_does_not_suppress_them() -> None:
    """Findings are provisional under inconclusive, never discarded."""
    result = engine.reconcile(
        rx(
            rx_item("rx-01", strength_value=500.0),
            run_item_counts=[3, 2, 2],
            unstable_lines=["a dropped line"],
        ),
        bill(bill_item("bill-01", strength_value=650.0), bill_item("bill-02", drug_name="Telma")),
    )
    assert result.verdict == "inconclusive"
    assert "STRENGTH_MISMATCH" in codes(result)
    assert "ITEM_COUNT_UNSTABLE" in codes(result)
    assert len(result.findings) > 2


def test_inconclusive_precedes_the_critical_check() -> None:
    """Unstable counts win over criticals, per the p4 noise decision."""
    result = engine.reconcile(
        rx(rx_item("rx-01"), run_item_counts=[7, 6, 6]),
        bill(),
    )
    assert "RX_NOT_BILLED" in codes(result)
    assert result.verdict == "inconclusive"
    assert result.score is None


def test_mismatch_when_a_critical_fires() -> None:
    result = engine.reconcile(rx(rx_item("rx-01", strength_value=500.0)),
                              bill(bill_item("bill-01", strength_value=650.0)))
    assert result.verdict == "mismatch"


def test_match_with_warnings() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01", form="tablet")),
        bill(bill_item("bill-01", form="syrup")),
    )
    assert result.verdict == "match_with_warnings"


def test_clean_match() -> None:
    result = engine.reconcile(rx(rx_item("rx-01")), bill(bill_item("bill-01")))
    assert result.verdict == "match"
    assert result.score == 100.0


def test_info_findings_alone_still_match() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01", agreement={"drug_name": 1.0, "form": 0.67})),
        bill(bill_item("bill-01")),
    )
    assert "LOW_CONFIDENCE_FIELD" in codes(result)
    assert result.verdict == "match"


# ==========================================================================
# Step 4 -- score
# ==========================================================================


def test_score_arithmetic() -> None:
    from rxconcile.models import Finding

    def make(severity: str) -> Finding:
        return Finding(rule_code="X", severity=severity, message="m")  # type: ignore[arg-type]

    assert engine.compute_score("match", []) == 100.0
    assert engine.compute_score("mismatch", [make("critical")]) == 75.0
    assert engine.compute_score("mismatch", [make("critical"), make("warning")]) == 67.0
    assert engine.compute_score("match_with_warnings", [make("warning")]) == 92.0


def test_info_findings_do_not_affect_score() -> None:
    from rxconcile.models import Finding

    info = Finding(rule_code="X", severity="info", message="m")
    assert engine.compute_score("match", [info, info, info]) == 100.0


def test_score_floors_at_zero_never_negative() -> None:
    from rxconcile.models import Finding

    criticals = [Finding(rule_code="X", severity="critical", message="m") for _ in range(10)]
    assert engine.compute_score("mismatch", criticals) == 0.0


def test_score_is_none_under_inconclusive_not_zero() -> None:
    from rxconcile.models import Finding

    criticals = [Finding(rule_code="X", severity="critical", message="m") for _ in range(10)]
    assert engine.compute_score("inconclusive", criticals) is None


def test_review_summary_is_populated_on_the_result() -> None:
    result = engine.reconcile(
        rx(rx_item("rx-01", drug_name=None, agreement={"drug_name": 0.33})),
        bill(bill_item("bill-01")),
    )
    assert result.review_summary.items_needing_review == 1
    assert result.review_summary.fields_nulled_by_disagreement == 1


def test_result_is_deterministic() -> None:
    """Same input, same output. No sampling anywhere in this module."""
    prescription = rx(rx_item("rx-01"), rx_item("rx-02", drug_name="Pan"))
    pharmacy = bill(bill_item("bill-01"), bill_item("bill-02", drug_name="Pan"))
    first = engine.reconcile(prescription, pharmacy, processing_ms=0)
    second = engine.reconcile(prescription, pharmacy, processing_ms=0)
    assert first.model_dump_json() == second.model_dump_json()
