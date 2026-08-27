"""Extraction layer tests. No network: the model call is always stubbed."""

from __future__ import annotations

import io
import json
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from google.genai import types
from PIL import Image

from rxconcile.extract import _runner, cache, preprocess
from rxconcile.extract.bill import build_bill
from rxconcile.extract.dto import (
    BilledItemDTO,
    PharmacyBillDTO,
    PrescribedItemDTO,
    PrescriptionDTO,
)
from rxconcile.extract.errors import (
    ExtractionError,
    ImageTooLargeError,
    UnreadableImageError,
)
from rxconcile.extract.preprocess import prepare_image
from rxconcile.extract.prescription import build_prescription


def png_bytes(size: tuple[int, int] = (120, 80), colour: str = "red") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


# --------------------------------------------------------------------------
# preprocess
# --------------------------------------------------------------------------


def test_downscales_longest_edge() -> None:
    prepared = prepare_image(png_bytes((5000, 2500)))
    assert max(prepared.width, prepared.height) == preprocess.MAX_EDGE_PX
    assert prepared.width == 2000
    assert prepared.height == 1000


def test_small_image_is_not_upscaled() -> None:
    prepared = prepare_image(png_bytes((300, 200)))
    assert (prepared.width, prepared.height) == (300, 200)


def test_output_is_jpeg() -> None:
    prepared = prepare_image(png_bytes())
    assert prepared.mime_type == "image/jpeg"
    with Image.open(io.BytesIO(prepared.data)) as reopened:
        assert reopened.format == "JPEG"


def test_hash_is_of_original_bytes() -> None:
    import hashlib

    raw = png_bytes()
    assert prepare_image(raw).sha256 == hashlib.sha256(raw).hexdigest()


def test_exif_orientation_is_applied() -> None:
    """A portrait image tagged orientation=6 must come back upright."""
    buffer = io.BytesIO()
    image = Image.new("RGB", (100, 300), "blue")
    exif = image.getexif()
    exif[274] = 6  # rotate 90 CW
    image.save(buffer, format="JPEG", exif=exif)
    prepared = prepare_image(buffer.getvalue())
    assert (prepared.width, prepared.height) == (300, 100)


def test_oversized_image_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        type(preprocess.settings), "max_upload_bytes", property(lambda _self: 10)
    )
    with pytest.raises(ImageTooLargeError, match="MAX_UPLOAD_MB"):
        prepare_image(png_bytes())


def test_non_image_bytes_rejected() -> None:
    with pytest.raises(UnreadableImageError):
        prepare_image(b"this is not an image")


def test_empty_bytes_rejected() -> None:
    with pytest.raises(UnreadableImageError):
        prepare_image(b"")


# --------------------------------------------------------------------------
# cache keying
# --------------------------------------------------------------------------


def test_cache_key_changes_with_prompt_version() -> None:
    """Editing a prompt must invalidate cached results, or tuning does nothing."""
    base = {"image_sha256": "abc", "doc_type": "prescription", "model": "m"}
    assert cache.cache_key(**base, prompt_version="v1") != cache.cache_key(
        **base, prompt_version="v2"
    )


def test_cache_key_changes_with_model_and_doc_type() -> None:
    base = {"image_sha256": "abc", "prompt_version": "v1"}
    assert cache.cache_key(**base, doc_type="bill", model="m") != cache.cache_key(
        **base, doc_type="prescription", model="m"
    )
    assert cache.cache_key(**base, doc_type="bill", model="m1") != cache.cache_key(
        **base, doc_type="bill", model="m2"
    )


def test_cache_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    cache.store("k1", {"hello": "world"})
    assert cache.load("k1") == {"hello": "world"}
    assert cache.load("missing") is None


def test_corrupt_cache_entry_is_a_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    (tmp_path / "bad.json").write_text("{not json")
    assert cache.load("bad") is None


# --------------------------------------------------------------------------
# identifiers and date resolution -- owned by Python, not the model
# --------------------------------------------------------------------------


def test_ids_are_sequential_and_padded() -> None:
    assert _runner.assign_ids("rx", 3) == ["rx-01", "rx-02", "rx-03"]
    assert _runner.assign_ids("bill", 0) == []
    assert _runner.assign_ids("rx", 100)[-1] == "rx-100"


def test_model_supplied_item_id_is_discarded() -> None:
    """extra='ignore' means an item_id from the model never reaches the domain."""
    dto = PrescribedItemDTO.model_validate(
        {"raw_text": "T. PCM", "confidence": 0.9, "item_id": "MODEL-INVENTED-99"}
    )
    assert not hasattr(dto, "item_id")


def test_python_assigns_ids_over_model_output() -> None:
    dto = PrescriptionDTO.model_validate(
        {
            "overall_legibility": 0.9,
            "items": [
                {"raw_text": "a", "confidence": 0.9, "item_id": "zzz"},
                {"raw_text": "a", "confidence": 0.9, "item_id": "zzz"},
            ],
        }
    )
    prescription = build_prescription([dto])
    assert [i.item_id for i in prescription.items] == ["rx-01", "rx-02"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-08-20", "2026-08-20"),
        ("25/12/2026", "2026-12-25"),
        ("3 Apr 2026", "2026-04-03"),
        ("20-08-2026", "2026-08-20"),
    ],
)
def test_unambiguous_dates_resolve(raw: str, expected: str) -> None:
    resolved, warning = _runner.resolve_date(raw)
    assert resolved is not None
    assert resolved.isoformat() == expected
    assert warning is None


@pytest.mark.parametrize("raw", ["03/04/26", "01/02/2026", "11/12/26"])
def test_ambiguous_dates_become_null_with_a_warning(raw: str) -> None:
    resolved, warning = _runner.resolve_date(raw)
    assert resolved is None
    assert warning is not None and "ambiguous" in warning


def test_impossible_date_is_null() -> None:
    resolved, warning = _runner.resolve_date("31/02/2026")
    assert resolved is None
    assert warning is not None


def test_decimal_conversion_avoids_float_error() -> None:
    assert _runner.to_decimal(13.2) == Decimal("13.2")
    tenth, fifth = _runner.to_decimal(0.1), _runner.to_decimal(0.2)
    assert tenth is not None and fifth is not None
    assert tenth + fifth == Decimal("0.3")  # 0.1 + 0.2 != 0.3 in binary float
    assert _runner.to_decimal(None) is None


def test_confidence_is_clamped_not_rejected() -> None:
    assert _runner.clamp_unit(1.4) == 1.0
    assert _runner.clamp_unit(-0.2) == 0.0
    assert _runner.clamp_unit(None) == 0.0


# --------------------------------------------------------------------------
# DTO -> domain conversion
# --------------------------------------------------------------------------


def test_prescription_conversion_preserves_never_guess_nulls() -> None:
    dto = PrescriptionDTO(
        patient_age="6 months",
        date_issued_raw="03/04/26",
        overall_legibility=0.4,
        items=[
            PrescribedItemDTO(raw_text="Syp. [?] 5ml OD", drug_name=None, confidence=0.3),
            PrescribedItemDTO(raw_text="T. PCM 500", drug_name="Paracetamol", confidence=0.9),
        ],
    )
    prescription = build_prescription([dto])
    assert prescription.patient_age == "6 months"  # unit preserved, never normalised
    assert prescription.date_issued is None
    assert any("ambiguous" in w for w in prescription.warnings)
    assert prescription.items[0].drug_name is None
    assert prescription.items[0].raw_text == "Syp. [?] 5ml OD"
    assert any("no legible drug name" in w for w in prescription.warnings)


def test_bill_conversion_maps_money_and_pack_size() -> None:
    dto = PharmacyBillDTO(
        bill_date_raw="20-08-2026",
        currency=None,
        grand_total=360.64,
        items=[
            BilledItemDTO(
                raw_text="PARACETAMOL 500MG TAB 10'S",
                drug_name="Paracetamol",
                quantity=10.0,
                pack_size="10'S",
                unit_price=2.2,
                line_total=22.0,
                confidence=1.0,
            ),
            BilledItemDTO(
                raw_text="DELIVERY CHARGE", form="other", line_total=30.0, confidence=1.0
            ),
        ],
    )
    bill = build_bill([dto])
    assert [i.item_id for i in bill.items] == ["bill-01", "bill-02"]
    assert bill.items[0].pack_size == "10'S"  # preserved verbatim, unparsed
    assert bill.items[0].line_total == Decimal("22.0")
    assert bill.grand_total == Decimal("360.64")
    assert bill.currency == "INR"  # default applied
    assert bill.items[1].form == "other"


def test_bill_currency_normalised() -> None:
    from rxconcile.extract.bill import _currency

    assert _currency("inr") == "INR"
    assert _currency("rupees") == "INR"
    assert _currency(None) == "INR"
    assert _currency("USD") == "USD"


# --------------------------------------------------------------------------
# schema retry
# --------------------------------------------------------------------------


class StubResult:
    def __init__(self, text: str) -> None:
        self.text = text
        self.model = "stub-model"


def install_responses(
    monkeypatch: pytest.MonkeyPatch, responses: list[str]
) -> list[str]:
    """Stub generate_content, returning each response in turn. Records prompts."""
    seen: list[str] = []
    queue = list(responses)

    def fake(
        contents: Sequence[types.Part], *, model: object = None, config: object = None
    ) -> StubResult:
        seen.append(str(contents[-1].text))
        return StubResult(queue.pop(0))

    monkeypatch.setattr(_runner, "generate_content", fake)
    return seen


@pytest.fixture
def image() -> preprocess.PreparedImage:
    return prepare_image(png_bytes())


def test_valid_first_response_is_returned(
    monkeypatch: pytest.MonkeyPatch, image: preprocess.PreparedImage
) -> None:
    prompts_seen = install_responses(
        monkeypatch, [json.dumps({"overall_legibility": 0.8, "items": []})]
    )
    dto = _runner.run_extraction(
        dto_type=PrescriptionDTO,
        instruction="INSTRUCTION",
        image=image,
        doc_type="prescription",
        use_cache=False,
    )
    assert dto.overall_legibility == 0.8
    assert len(prompts_seen) == 1


def test_schema_failure_retries_once_with_the_error_appended(
    monkeypatch: pytest.MonkeyPatch, image: preprocess.PreparedImage
) -> None:
    prompts_seen = install_responses(
        monkeypatch,
        ["{ not valid json", json.dumps({"overall_legibility": 0.5, "items": []})],
    )
    dto = _runner.run_extraction(
        dto_type=PrescriptionDTO,
        instruction="INSTRUCTION",
        image=image,
        doc_type="prescription",
        use_cache=False,
    )
    assert dto.overall_legibility == 0.5
    assert len(prompts_seen) == 2
    assert "FAILED VALIDATION" in prompts_seen[1]
    assert "INSTRUCTION" in prompts_seen[1]


def test_two_schema_failures_raise_and_return_nothing_partial(
    monkeypatch: pytest.MonkeyPatch, image: preprocess.PreparedImage
) -> None:
    install_responses(monkeypatch, ["{ bad", "{ also bad"])
    with pytest.raises(ExtractionError, match="both attempts"):
        _runner.run_extraction(
            dto_type=PrescriptionDTO,
            instruction="I",
            image=image,
            doc_type="prescription",
            use_cache=False,
        )


def test_empty_response_is_retried_then_raises(
    monkeypatch: pytest.MonkeyPatch, image: preprocess.PreparedImage
) -> None:
    install_responses(monkeypatch, ["", ""])
    with pytest.raises(ExtractionError):
        _runner.run_extraction(
            dto_type=PrescriptionDTO,
            instruction="I",
            image=image,
            doc_type="prescription",
            use_cache=False,
        )


def test_cache_hit_skips_the_model_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, image: preprocess.PreparedImage
) -> None:
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    install_responses(monkeypatch, [json.dumps({"overall_legibility": 0.7, "items": []})])

    kwargs: dict[str, Any] = {
        "dto_type": PrescriptionDTO,
        "instruction": "I",
        "image": image,
        "doc_type": "prescription",
        "use_cache": True,
    }
    first = _runner.run_extraction(**kwargs)
    # Only one stub response was queued; a second call would raise IndexError.
    second = _runner.run_extraction(**kwargs)
    assert first.overall_legibility == second.overall_legibility == 0.7
