"""Self-consistency resolution across N extraction runs."""

from __future__ import annotations

import pytest

from rxconcile.extract import consensus
from rxconcile.extract.dto import PrescribedItemDTO, PrescriptionDTO
from rxconcile.extract.prescription import build_prescription


def item(raw: str, **kwargs: object) -> PrescribedItemDTO:
    return PrescribedItemDTO(raw_text=raw, **kwargs)  # type: ignore[arg-type]


def run(*items: PrescribedItemDTO, legibility: float = 0.8) -> PrescriptionDTO:
    return PrescriptionDTO(items=list(items), overall_legibility=legibility)


# --------------------------------------------------------------------------
# The resolution table from DESIGN_DECISIONS section 2
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "expected_value", "expected_agreement"),
    [
        (["A", "A", "A"], "A", 1.0),
        (["A", "A", "B"], "A", 0.67),
        (["A", "A", None], "A", 0.67),
        ([None, None, "A"], None, 0.67),
        (["A", "B", "C"], None, 0.33),
        ([None, None, None], None, 1.0),
    ],
)
def test_resolution_table(
    values: list[str | None], expected_value: str | None, expected_agreement: float
) -> None:
    result = consensus.resolve_values(values, run_count=len(values))
    assert result.value == expected_value
    assert result.agreement == pytest.approx(expected_agreement)


def test_three_distinct_readings_resolve_to_null_not_a_plurality_of_one() -> None:
    """The model could not read it; picking one of three would invent an answer."""
    result = consensus.resolve_values(["Clogen", None, "Clocen"], run_count=3)
    assert result.value is None
    assert result.agreement == 0.33


def test_null_is_an_observation_that_votes() -> None:
    result = consensus.resolve_values(["Dolo", None, None], run_count=3)
    assert result.value is None
    assert result.agreement == 0.67


def test_single_run_reports_no_agreement() -> None:
    """One run has no agreement; 1.0 would be a lie."""
    result = consensus.resolve_values(["A"], run_count=1)
    assert result.value == "A"
    assert result.agreement is None


def test_float_noise_does_not_break_agreement() -> None:
    result = consensus.resolve_values([500.0, 500.0, 500.0], run_count=3)
    assert result.agreement == 1.0


# --------------------------------------------------------------------------
# Item alignment
# --------------------------------------------------------------------------


def test_identical_runs_align_one_to_one() -> None:
    runs = [[item("TAB DOLO 650"), item("CAP PAN 40")] for _ in range(3)]
    clusters = consensus.align_items(runs)
    assert len(clusters) == 2
    assert all(cluster.is_stable for cluster in clusters)


def test_alignment_survives_a_dropped_line() -> None:
    """A run that omits a line must not shift every later line by one."""
    full = [item("A LINE ONE"), item("B LINE TWO"), item("C LINE THREE")]
    short = [item("A LINE ONE"), item("C LINE THREE")]
    clusters = consensus.align_items([full, list(full), short])
    kept, unstable = consensus.split_clusters(clusters, run_count=3)
    assert len(kept) == 3
    assert unstable == ["B LINE TWO"]


def test_alignment_tolerates_whitespace_and_punctuation_drift() -> None:
    clusters = consensus.align_items(
        [
            [item("- T. Dexa (4) - BD D2-D4")],
            [item("T. Dexa(4) BD D2-D4")],
            [item("T. Dexa (4) - BD D2-D4")],
        ]
    )
    assert len(clusters) == 1
    assert clusters[0].present_count == 3


def test_line_in_a_minority_of_runs_is_dropped_but_reported() -> None:
    runs = [[item("KEEP ME")], [item("KEEP ME")], [item("KEEP ME"), item("ONLY ONCE")]]
    clusters = consensus.align_items(runs)
    kept, unstable = consensus.split_clusters(clusters, run_count=3)
    assert [c.canonical_raw_text for c in kept] == ["KEEP ME"]
    assert "ONLY ONCE" in unstable


def test_majority_threshold() -> None:
    assert consensus.majority_threshold(1) == 1
    assert consensus.majority_threshold(3) == 2
    assert consensus.majority_threshold(5) == 3


# --------------------------------------------------------------------------
# Whole-document assembly
# --------------------------------------------------------------------------


def test_agreement_is_recorded_per_field() -> None:
    runs = [
        run(item("TAB DOLO 650", drug_name="Dolo", strength_value=650.0)),
        run(item("TAB DOLO 650", drug_name="Dolo", strength_value=650.0)),
        run(item("TAB DOLO 650", drug_name="Dolo", strength_value=500.0)),
    ]
    doc = build_prescription(runs)
    assert doc.items[0].agreement is not None
    assert doc.items[0].agreement["drug_name"] == 1.0
    assert doc.items[0].agreement["strength_value"] == 0.67
    assert doc.items[0].strength_value == 650.0


def test_unreadable_field_resolves_to_null_with_low_agreement() -> None:
    runs = [
        run(item("- Clogen - TDS", drug_name="Clogen")),
        run(item("- Clogen - TDS", drug_name=None)),
        run(item("- Clogen - TDS", drug_name="Clocen")),
    ]
    doc = build_prescription(runs)
    assert doc.items[0].drug_name is None
    assert doc.items[0].agreement is not None
    assert doc.items[0].agreement["drug_name"] == 0.33


def test_item_count_instability_is_recorded_on_the_document() -> None:
    runs = [
        run(item("LINE A"), item("LINE B")),
        run(item("LINE A"), item("LINE B")),
        run(item("LINE A")),
    ]
    doc = build_prescription(runs)
    assert doc.run_item_counts == [2, 2, 1]
    assert doc.unstable_lines == ["LINE B"]
    assert any("some extraction runs but not all" in w for w in doc.warnings)


def test_stable_document_reports_no_instability() -> None:
    runs = [run(item("LINE A"), item("LINE B")) for _ in range(3)]
    doc = build_prescription(runs)
    assert doc.run_item_counts == [2, 2, 2]
    assert doc.unstable_lines == []


def test_single_run_reports_agreement_as_none_throughout() -> None:
    doc = build_prescription([run(item("TAB DOLO 650", drug_name="Dolo"))])
    assert doc.run_item_counts == [1]
    assert doc.items[0].agreement is None
    assert doc.items[0].drug_name == "Dolo"


def test_ids_are_assigned_after_consensus() -> None:
    runs = [run(item("LINE A"), item("LINE B")) for _ in range(3)]
    doc = build_prescription(runs)
    assert [i.item_id for i in doc.items] == ["rx-01", "rx-02"]


def test_raw_text_is_never_nulled_even_when_runs_disagree() -> None:
    """raw_text is the evidence a reviewer checks; blanking it destroys that.

    Uses realistic transcription drift -- the same line read three slightly
    different ways -- which still aligns to one cluster.
    """
    runs = [
        run(item("- T. Ondam (4) - BD D2-D4")),
        run(item("- T. Ondan (4) - BD D2-D4")),
        run(item("- T Ondem 4 - BD D2-D4")),
    ]
    doc = build_prescription(runs)
    assert len(doc.items) == 1
    assert doc.items[0].raw_text != ""
    assert doc.items[0].agreement is not None
    assert doc.items[0].agreement["raw_text"] == 0.33


def test_wildly_divergent_text_is_treated_as_separate_lines() -> None:
    """Below the alignment threshold, readings are different lines, not one line.

    Each then appears in only one run, falls short of a majority, and is
    reported as unstable rather than silently kept.
    """
    runs = [run(item("TAB AUGMENTIN 625")), run(item("CAP OMEZ 20")), run(item("SYP ASCORIL"))]
    doc = build_prescription(runs)
    assert doc.items == []
    assert len(doc.unstable_lines) == 3


def test_empty_run_list_raises() -> None:
    from rxconcile.extract.errors import ExtractionError

    with pytest.raises(ExtractionError):
        build_prescription([])


def test_document_fields_use_the_same_majority_rule() -> None:
    runs = [
        PrescriptionDTO(patient_name="R. Sharma", overall_legibility=0.8),
        PrescriptionDTO(patient_name="R. Sharma", overall_legibility=0.8),
        PrescriptionDTO(patient_name="R Sharme", overall_legibility=0.8),
    ]
    assert build_prescription(runs).patient_name == "R. Sharma"


def test_confidence_survives_but_agreement_is_the_signal() -> None:
    """The model's own score is retained for the record only."""
    runs = [run(item("A LINE", drug_name="Dolo", confidence=0.9)) for _ in range(3)]
    doc = build_prescription(runs)
    assert doc.items[0].confidence == pytest.approx(0.9)
    assert doc.items[0].agreement is not None


# --------------------------------------------------------------------------
# Bounding boxes -- resolved by overlap, not equality
# --------------------------------------------------------------------------


def test_iou_of_identical_boxes_is_one() -> None:
    box = [0.1, 0.1, 0.5, 0.2]
    assert consensus.iou(tuple(box), tuple(box)) == pytest.approx(1.0)  # type: ignore[arg-type]


def test_iou_of_disjoint_boxes_is_zero() -> None:
    assert consensus.iou((0.1, 0.1, 0.4, 0.2), (0.1, 0.5, 0.4, 0.6)) == 0.0


def test_jitter_on_the_same_line_clears_the_threshold() -> None:
    """Three runs never return identical floats; small jitter must still agree."""
    a = (0.10, 0.100, 0.50, 0.200)
    b = (0.11, 0.105, 0.51, 0.205)
    assert consensus.iou(a, b) >= consensus.IOU_AGREEMENT_THRESHOLD


def test_adjacent_lines_do_not_clear_the_threshold() -> None:
    """The failure a provenance highlight must never make.

    Two boxes on neighbouring lines of a prescription must not count as the same
    location, or the UI would point a reviewer at the wrong scrawl.
    """
    line_one = (0.1, 0.40, 0.9, 0.46)
    line_two = (0.1, 0.47, 0.9, 0.53)
    assert consensus.iou(line_one, line_two) < consensus.IOU_AGREEMENT_THRESHOLD


def test_three_agreeing_boxes_resolve_to_their_mean() -> None:
    boxes = [[0.1, 0.1, 0.5, 0.2], [0.11, 0.105, 0.51, 0.205], [0.1, 0.1, 0.5, 0.2]]
    result = consensus.resolve_bbox(boxes, run_count=3)
    assert result.agreement == 1.0
    assert result.value is not None
    assert result.value[0] == pytest.approx(0.1033, abs=1e-3)


def test_scattered_boxes_resolve_to_none() -> None:
    """A location the runs cannot reproduce is a guess, not a location."""
    boxes = [[0.1, 0.1, 0.3, 0.2], [0.1, 0.5, 0.3, 0.6], [0.6, 0.7, 0.9, 0.8]]
    result = consensus.resolve_bbox(boxes, run_count=3)
    assert result.value is None
    assert result.agreement == 0.33


def test_two_of_three_boxes_agree() -> None:
    boxes = [[0.1, 0.1, 0.5, 0.2], [0.105, 0.1, 0.505, 0.2], [0.1, 0.8, 0.5, 0.9]]
    result = consensus.resolve_bbox(boxes, run_count=3)
    assert result.agreement == 0.67
    assert result.value is not None


def test_a_box_found_by_only_one_run_is_discarded() -> None:
    result = consensus.resolve_bbox([[0.1, 0.1, 0.5, 0.2], None, None], run_count=3)
    assert result.value is None
    assert result.agreement == 0.33


def test_malformed_boxes_are_rejected() -> None:
    """Inverted, out-of-range and wrong-length boxes are not locations."""
    for bad in ([0.5, 0.1, 0.1, 0.2], [0.1, 0.1, 1.5, 0.2], [0.1, 0.1, 0.5], "nope"):
        assert consensus.resolve_bbox([bad, bad, bad], run_count=3).value is None


def test_single_run_bbox_reports_no_agreement() -> None:
    result = consensus.resolve_bbox([[0.1, 0.1, 0.5, 0.2]], run_count=1)
    assert result.value is not None
    assert result.agreement is None


def test_bbox_survives_into_the_domain_model() -> None:
    runs = [
        run(item("TAB DOLO 650", drug_name="Dolo", bbox=[0.1, 0.1, 0.5, 0.2]))
        for _ in range(3)
    ]
    doc = build_prescription(runs)
    assert doc.items[0].bbox is not None
    assert doc.items[0].agreement is not None
    assert doc.items[0].agreement["bbox"] == 1.0


def test_unlocatable_line_keeps_a_null_bbox() -> None:
    runs = [run(item("TAB DOLO 650", drug_name="Dolo", bbox=None)) for _ in range(3)]
    doc = build_prescription(runs)
    assert doc.items[0].bbox is None
