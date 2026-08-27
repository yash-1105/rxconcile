"""Data contract tests: fixtures parse, identity holds, bad references fail loudly."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from rxconcile.models import (
    BilledItem,
    Finding,
    MatchedPair,
    PharmacyBill,
    PrescribedItem,
    Prescription,
    ReconciliationResult,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_NAMES = [
    "01_clean_match",
    "02_strength_mismatch_extra_billed",
    "03_illegible_prescription",
]


def load_fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURE_DIR / f"{name}.json").read_text())
    return data


def build_result(fixture: dict[str, Any], **overrides: object) -> ReconciliationResult:
    """Assemble a ReconciliationResult from a fixture's ``expected`` block."""
    expected = fixture["expected"]
    kwargs: dict[str, Any] = {
        "verdict": expected["verdict"],
        "score": expected["score"],
        "findings": expected.get("findings", []),
        "matched_pairs": expected["matched_pairs"],
        "unmatched_prescribed": expected["unmatched_prescribed"],
        "unmatched_billed": expected["unmatched_billed"],
        "prescription": fixture["prescription"],
        "bill": fixture["bill"],
        "processing_ms": 1234,
    }
    kwargs.update(overrides)
    return ReconciliationResult.model_validate(kwargs)


# --------------------------------------------------------------------------
# Fixtures parse
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_documents_parse(name: str) -> None:
    fixture = load_fixture(name)
    prescription = Prescription.model_validate(fixture["prescription"])
    bill = PharmacyBill.model_validate(fixture["bill"])
    assert prescription.items
    assert bill.items


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_expected_block_builds_a_valid_result(name: str) -> None:
    """The expected outcome must satisfy referential integrity."""
    result = build_result(load_fixture(name))
    assert result.processing_ms == 1234


def test_illegible_fixture_keeps_nulls_rather_than_guessing() -> None:
    prescription = Prescription.model_validate(
        load_fixture("03_illegible_prescription")["prescription"]
    )
    unreadable = next(i for i in prescription.items if i.item_id == "rx-01")
    assert unreadable.drug_name is None
    assert unreadable.strength_value is None
    assert unreadable.confidence < 0.25
    assert prescription.date_issued is None


# --------------------------------------------------------------------------
# Identity rule -- the regression test for keying on item_id, not raw_text
# --------------------------------------------------------------------------


def test_identical_raw_text_items_are_distinct_and_pair_to_distinct_bills() -> None:
    """rx-01 and rx-02 share raw_text byte-for-byte but are different items.

    This is the guard on the whole identity decision. Keying cross-references on
    raw_text would collapse these two into one, so the assertions below fail the
    moment anyone reverts to raw_text keying.
    """
    fixture = load_fixture("01_clean_match")
    prescription = Prescription.model_validate(fixture["prescription"])

    first = next(i for i in prescription.items if i.item_id == "rx-01")
    second = next(i for i in prescription.items if i.item_id == "rx-02")

    # Byte-identical display text, genuinely different items.
    assert first.raw_text == second.raw_text
    assert first.item_id != second.item_id
    assert first.duration_days != second.duration_days

    # raw_text is ambiguous as a key; item_id is not.
    raw_texts = [i.raw_text for i in prescription.items]
    item_ids = [i.item_id for i in prescription.items]
    assert len(set(raw_texts)) < len(raw_texts), "fixture must contain a raw_text collision"
    assert len(set(item_ids)) == len(item_ids)

    # The two collide only on raw_text, and still pair to DISTINCT billed items.
    pairs = {p["prescribed_id"]: p["billed_id"] for p in fixture["expected"]["matched_pairs"]}
    assert pairs["rx-01"] != pairs["rx-02"]

    result = build_result(fixture)
    billed_for = {p.prescribed_id: p.billed_id for p in result.matched_pairs}
    assert billed_for["rx-01"] == "bill-01"
    assert billed_for["rx-02"] == "bill-02"
    assert len({p.billed_id for p in result.matched_pairs}) == len(result.matched_pairs)


def test_bill_also_tolerates_repeated_raw_text() -> None:
    """Bills repeat strings constantly; identical raw_text must stay separable."""
    bill = PharmacyBill.model_validate(load_fixture("01_clean_match")["bill"])
    first = next(i for i in bill.items if i.item_id == "bill-01")
    second = next(i for i in bill.items if i.item_id == "bill-02")
    assert first.raw_text == second.raw_text
    assert first.quantity != second.quantity
    assert len(bill.item_ids) == len(bill.items)


# --------------------------------------------------------------------------
# Negative: duplicate item_id within one document
# --------------------------------------------------------------------------


def prescribed(item_id: str, **overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "item_id": item_id,
        "raw_text": "TAB PARACETAMOL 500MG",
        "drug_name": "Paracetamol",
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


def billed(item_id: str, **overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "item_id": item_id,
        "raw_text": "PARACETAMOL 500MG TAB",
        "drug_name": "Paracetamol",
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


def test_duplicate_prescribed_item_id_is_a_validation_error() -> None:
    with pytest.raises(ValidationError, match=r"duplicate PrescribedItem.item_id"):
        Prescription.model_validate(
            {
                "overall_legibility": 0.9,
                "items": [prescribed("rx-01"), prescribed("rx-01", raw_text="OTHER")],
            }
        )


def test_duplicate_billed_item_id_is_a_validation_error() -> None:
    with pytest.raises(ValidationError, match=r"duplicate BilledItem.item_id"):
        PharmacyBill.model_validate(
            {"items": [billed("bill-01"), billed("bill-01", raw_text="OTHER")]}
        )


def test_identical_raw_text_with_distinct_ids_is_accepted() -> None:
    """The counterpart: colliding raw_text is legal, colliding item_id is not."""
    prescription = Prescription.model_validate(
        {"overall_legibility": 0.9, "items": [prescribed("rx-01"), prescribed("rx-02")]}
    )
    assert len(prescription.items) == 2
    assert prescription.items[0].raw_text == prescription.items[1].raw_text


# --------------------------------------------------------------------------
# Negative: dangling references in ReconciliationResult
# --------------------------------------------------------------------------


def test_dangling_finding_reference_is_a_validation_error() -> None:
    fixture = load_fixture("01_clean_match")
    dangling = Finding(
        rule_code="STRENGTH_MISMATCH",
        severity="critical",
        message="Prescribed strength does not match billed strength.",
        prescribed_ref="rx-99",
        billed_ref=None,
    ).model_dump()
    with pytest.raises(ValidationError, match=r"rx-99.*not a PrescribedItem.item_id"):
        build_result(fixture, findings=[dangling])


def test_dangling_billed_finding_reference_is_a_validation_error() -> None:
    fixture = load_fixture("01_clean_match")
    dangling = Finding(
        rule_code="EXTRA_BILLED_ITEM",
        severity="warning",
        message="Billed item has no corresponding prescription line.",
        billed_ref="bill-99",
    ).model_dump()
    with pytest.raises(ValidationError, match=r"bill-99.*not a BilledItem.item_id"):
        build_result(fixture, findings=[dangling])


def test_dangling_matched_pair_is_a_validation_error() -> None:
    fixture = load_fixture("01_clean_match")
    pairs = [MatchedPair(prescribed_id="rx-01", billed_id="bill-99", similarity=1.0).model_dump()]
    with pytest.raises(ValidationError, match=r"bill-99.*not a BilledItem.item_id"):
        build_result(fixture, matched_pairs=pairs, unmatched_prescribed=["rx-02", "rx-03"])


def test_dangling_unmatched_id_is_a_validation_error() -> None:
    fixture = load_fixture("01_clean_match")
    with pytest.raises(ValidationError, match=r"unmatched_billed\[0\]='bill-99'"):
        build_result(fixture, unmatched_billed=["bill-99"])


def test_id_cannot_be_both_matched_and_unmatched() -> None:
    fixture = load_fixture("01_clean_match")
    with pytest.raises(ValidationError, match=r"appear in both a matched pair and"):
        build_result(fixture, unmatched_prescribed=["rx-01"])


def test_all_dangling_references_are_reported_together() -> None:
    """A caller fixing references should see every problem at once."""
    fixture = load_fixture("01_clean_match")
    with pytest.raises(ValidationError) as excinfo:
        build_result(
            fixture,
            matched_pairs=[
                MatchedPair(
                    prescribed_id="rx-77", billed_id="bill-88", similarity=0.5
                ).model_dump()
            ],
            unmatched_billed=["bill-99"],
        )
    message = str(excinfo.value)
    assert "rx-77" in message
    assert "bill-88" in message
    assert "bill-99" in message


def test_valid_result_round_trips_through_json() -> None:
    result = build_result(load_fixture("02_strength_mismatch_extra_billed"))
    restored = ReconciliationResult.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.findings[0].rule_code == "STRENGTH_MISMATCH"
    assert restored.findings[0].detail["expected"]["value"] == 5.0


# --------------------------------------------------------------------------
# Field-level guards
# --------------------------------------------------------------------------


def test_confidence_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        PrescribedItem.model_validate(prescribed("rx-01", confidence=1.4))
    with pytest.raises(ValidationError):
        BilledItem.model_validate(billed("bill-01", confidence=-0.1))


def test_unknown_field_is_rejected() -> None:
    """extra='forbid' turns extractor schema drift into a loud failure."""
    with pytest.raises(ValidationError):
        PrescribedItem.model_validate(prescribed("rx-01", hallucinated_field="x"))


def test_empty_item_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PrescribedItem.model_validate(prescribed(""))


# --------------------------------------------------------------------------
# ReviewSummary
# --------------------------------------------------------------------------


def with_agreement(item: dict[str, Any], agreement: dict[str, float]) -> dict[str, Any]:
    return {**item, "agreement": agreement}


def test_review_summary_defaults_to_zero_when_nothing_is_shaky() -> None:
    result = build_result(load_fixture("01_clean_match"))
    assert result.review_summary.items_needing_review == 0
    assert result.review_summary.fields_nulled_by_disagreement == 0
    assert result.review_summary.unstable_line_count == 0
    assert result.review_summary.needs_attention is False


def test_review_summary_counts_items_not_fields() -> None:
    """An item with three shaky fields is one item needing review."""
    fixture = load_fixture("01_clean_match")
    fixture["prescription"]["items"][0]["agreement"] = {
        "drug_name": 0.67,
        "strength_value": 0.67,
        "frequency_raw": 0.33,
    }
    result = build_result(fixture)
    assert result.review_summary.items_needing_review == 1


def test_review_summary_counts_fields_nulled_by_disagreement() -> None:
    fixture = load_fixture("01_clean_match")
    item = fixture["prescription"]["items"][0]
    item["agreement"] = {"drug_name": 0.33, "salt": 0.33, "strength_value": 1.0}
    item["drug_name"] = None
    item["salt"] = None
    result = build_result(fixture)
    assert result.review_summary.fields_nulled_by_disagreement == 2
    assert result.review_summary.items_needing_review == 1


def test_review_summary_counts_unstable_lines_from_both_documents() -> None:
    fixture = load_fixture("01_clean_match")
    fixture["prescription"]["unstable_lines"] = ["- 6# (P+H)"]
    fixture["bill"]["unstable_lines"] = ["DELIVERY CHARGE", "MASK"]
    result = build_result(fixture)
    assert result.review_summary.unstable_line_count == 3
    assert result.review_summary.needs_attention is True


def test_full_agreement_is_not_flagged() -> None:
    fixture = load_fixture("01_clean_match")
    fixture["prescription"]["items"][0]["agreement"] = {"drug_name": 1.0, "salt": 1.0}
    assert build_result(fixture).review_summary.items_needing_review == 0


def test_single_run_agreement_is_not_counted_as_needing_review() -> None:
    """agreement=None means one run: no evidence either way, so no claim."""
    fixture = load_fixture("01_clean_match")
    fixture["prescription"]["items"][0]["agreement"] = None
    assert build_result(fixture).review_summary.items_needing_review == 0


def test_supplied_review_summary_is_overridden_not_trusted() -> None:
    """A caller cannot make the counts say something the documents do not."""
    fixture = load_fixture("01_clean_match")
    fixture["prescription"]["unstable_lines"] = ["one unstable line"]
    result = build_result(
        fixture,
        review_summary={
            "items_needing_review": 999,
            "fields_nulled_by_disagreement": 999,
            "unstable_line_count": 0,
        },
    )
    assert result.review_summary.items_needing_review == 0
    assert result.review_summary.unstable_line_count == 1


def test_review_summary_survives_json_round_trip() -> None:
    fixture = load_fixture("01_clean_match")
    fixture["prescription"]["items"][0]["agreement"] = {"drug_name": 0.67}
    fixture["prescription"]["unstable_lines"] = ["a line"]
    result = build_result(fixture)
    restored = ReconciliationResult.model_validate_json(result.model_dump_json())
    assert restored.review_summary == result.review_summary
    assert restored == result
