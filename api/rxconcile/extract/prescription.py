"""Prescription extraction: image in, consensus-resolved :class:`Prescription` out."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from rxconcile.config import settings
from rxconcile.extract import cache, consensus
from rxconcile.extract._runner import (
    assign_ids,
    clamp_unit,
    collect_runs,
    collect_runs_async,
    resolve_date,
)
from rxconcile.extract.dto import PrescriptionDTO
from rxconcile.extract.errors import ExtractionError
from rxconcile.extract.preprocess import PreparedImage, prepare_image
from rxconcile.extract.prompts import PRESCRIPTION_INSTRUCTION, PROMPT_VERSION
from rxconcile.models import PrescribedItem, PrescribedTest, Prescription

logger: Final = logging.getLogger(__name__)


def _as_bbox(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, list | tuple) or len(value) != 4:
        return None
    x0, y0, x1, y1 = (float(v) for v in value)
    return (x0, y0, x1, y1)

DOC_TYPE: Final[str] = "prescription"
ITEM_ID_PREFIX: Final[str] = "rx"
TEST_ID_PREFIX: Final[str] = "test"

#: Item fields carried through consensus. ``item_id`` is assigned by Python and
#: ``confidence`` is the model's own non-gating score, so neither is voted on.
ITEM_FIELDS: Final[tuple[str, ...]] = (
    "raw_text",
    "bbox",
    "drug_name",
    "salt",
    "strength_value",
    "strength_unit",
    "form",
    "dose_per_administration",
    "frequency_raw",
    "duration_raw",
    "duration_days",
    "route",
    "instructions",
)

#: Test fields carried through the same consensus machinery as items.
TEST_FIELDS: Final[tuple[str, ...]] = ("raw_text", "bbox", "test_name", "panel", "urgency")

#: Document-level scalars resolved by the same majority rule.
_DOC_FIELDS: Final[tuple[str, ...]] = (
    "patient_name",
    "patient_age",
    "patient_sex",
    "prescriber_name",
    "prescriber_reg_no",
    "clinic_name",
    "date_issued_raw",
    "diagnosis_text",
)


def _build_item(cluster: consensus.ItemCluster, item_id: str) -> PrescribedItem:
    resolved = {field: consensus.resolve_field(cluster, field) for field in ITEM_FIELDS}
    # Coordinates never repeat exactly, so boxes are resolved by overlap.
    resolved["bbox"] = consensus.resolve_bbox(
        [getattr(item, "bbox", None) for item in cluster.present],
        run_count=cluster.present_count,
    )
    agreement = {
        field: outcome.agreement
        for field, outcome in resolved.items()
        if outcome.agreement is not None
    }
    confidences = [float(getattr(item, "confidence", 0.0)) for item in cluster.present]
    duration_days = resolved["duration_days"].value
    if isinstance(duration_days, int | float) and duration_days < 0:
        duration_days = None

    return PrescribedItem(
        item_id=item_id,
        # Never nulled: raw_text is the evidence a reviewer checks against the
        # image. Its agreement ratio still records any disagreement.
        raw_text=cluster.canonical_raw_text,
        bbox=_as_bbox(resolved["bbox"].value),
        drug_name=resolved["drug_name"].value,
        salt=resolved["salt"].value,
        strength_value=resolved["strength_value"].value,
        strength_unit=resolved["strength_unit"].value,
        form=resolved["form"].value,
        dose_per_administration=resolved["dose_per_administration"].value,
        frequency_raw=resolved["frequency_raw"].value,
        duration_raw=resolved["duration_raw"].value,
        duration_days=int(duration_days) if duration_days is not None else None,
        route=resolved["route"].value,
        instructions=resolved["instructions"].value,
        agreement=agreement or None,
        confidence=clamp_unit(sum(confidences) / len(confidences) if confidences else 0.0),
    )


def _build_test(cluster: consensus.ItemCluster, item_id: str) -> PrescribedTest:
    resolved = {field: consensus.resolve_field(cluster, field) for field in TEST_FIELDS}
    resolved["bbox"] = consensus.resolve_bbox(
        [getattr(test, "bbox", None) for test in cluster.present],
        run_count=cluster.present_count,
    )
    agreement = {
        field: outcome.agreement
        for field, outcome in resolved.items()
        if outcome.agreement is not None
    }
    confidences = [float(getattr(test, "confidence", 0.0)) for test in cluster.present]
    return PrescribedTest(
        item_id=item_id,
        # Never nulled, exactly as for items: raw_text is the reviewer's evidence.
        raw_text=cluster.canonical_raw_text,
        bbox=_as_bbox(resolved["bbox"].value),
        test_name=resolved["test_name"].value,
        panel=resolved["panel"].value,
        urgency=resolved["urgency"].value,
        agreement=agreement or None,
        confidence=clamp_unit(sum(confidences) / len(confidences) if confidences else 0.0),
    )


def build_prescription(runs: list[PrescriptionDTO]) -> Prescription:
    """Resolve N extraction runs into one prescription.

    With a single run this is a straight conversion and every ``agreement`` is
    None -- one run has no agreement to report.
    """
    if not runs:
        raise ExtractionError("no extraction runs to resolve")

    run_count = len(runs)
    clusters = consensus.align_items([list(run.items) for run in runs])
    kept, unstable = consensus.split_clusters(clusters, run_count=run_count)

    item_ids = assign_ids(ITEM_ID_PREFIX, len(kept))
    items = [_build_item(cluster, item_id) for item_id, cluster in zip(item_ids, kept, strict=True)]

    test_clusters = consensus.align_items([list(run.tests) for run in runs])
    kept_tests, unstable_tests = consensus.split_clusters(test_clusters, run_count=run_count)
    test_ids = assign_ids(TEST_ID_PREFIX, len(kept_tests))
    tests = [
        _build_test(cluster, test_id)
        for test_id, cluster in zip(test_ids, kept_tests, strict=True)
    ]
    # A layout observation, resolved by majority like any other scalar. Only an
    # explicit False means "no investigations section"; null stays null.
    investigations_present = consensus.resolve_values(
        [run.investigations_present for run in runs], run_count=run_count
    ).value
    if tests and investigations_present is not True:
        # Lines were read out of a section the model did not flag. The section
        # demonstrably exists.
        investigations_present = True

    doc = {
        field: consensus.resolve_values(
            [getattr(run, field) for run in runs], run_count=run_count
        ).value
        for field in _DOC_FIELDS
    }
    date_issued, date_warning = resolve_date(doc["date_issued_raw"])

    warnings: list[str] = []
    for run in runs:
        for warning in run.warnings:
            if warning not in warnings:
                warnings.append(warning)
    if date_warning:
        warnings.append(date_warning)

    unnamed = sum(1 for item in items if item.drug_name is None)
    if unnamed:
        warnings.append(
            f"{unnamed} of {len(items)} prescribed item(s) had no legible drug name "
            "and were left null rather than guessed."
        )
    unnamed_tests = sum(1 for test in tests if test.test_name is None)
    if unnamed_tests:
        warnings.append(
            f"{unnamed_tests} of {len(tests)} ordered test(s) had no legible name and "
            "were left null rather than guessed."
        )
    if investigations_present and not tests:
        # The distinction the whole feature turns on. Say it in words, not by
        # an empty list that reads as a clean result.
        warnings.append(
            "An investigations section is present on the page but no test line could "
            "be read from it. This is NOT the same as no tests being ordered."
        )
    if unstable_tests:
        warnings.append(
            f"{len(unstable_tests)} test line(s) appeared in some extraction runs but "
            f"not all; test counts across runs were {[len(run.tests) for run in runs]}."
        )
    if unstable:
        warnings.append(
            f"{len(unstable)} line(s) appeared in some extraction runs but not all; "
            "item counts across runs were "
            f"{[len(run.items) for run in runs]}."
        )

    legibilities = [float(run.overall_legibility) for run in runs]
    return Prescription(
        patient_name=doc["patient_name"],
        patient_age=doc["patient_age"],
        patient_sex=doc["patient_sex"],
        prescriber_name=doc["prescriber_name"],
        prescriber_reg_no=doc["prescriber_reg_no"],
        clinic_name=doc["clinic_name"],
        date_issued=date_issued,
        diagnosis_text=doc["diagnosis_text"],
        items=items,
        tests=tests,
        investigations_present=(
            investigations_present if isinstance(investigations_present, bool) else None
        ),
        overall_legibility=clamp_unit(sum(legibilities) / len(legibilities)),
        run_item_counts=[len(run.items) for run in runs],
        unstable_lines=unstable + unstable_tests,
        warnings=warnings,
    )


def extract_prescription(
    source: Path | bytes | PreparedImage,
    *,
    model: str | None = None,
    use_cache: bool = True,
    runs: int | None = None,
) -> Prescription:
    """Extract a prescription, resolving N runs by per-field agreement.

    Args:
        source: image path, raw bytes, or an already-prepared image.
        model: override the configured extraction model.
        use_cache: read and write the on-disk cache of the resolved document.
        runs: override ``EXTRACTION_RUNS``. N=1 skips consensus and reports
            agreement as None.

    Raises:
        ExtractionError: extraction or validation failed. No partial object is
            ever returned.
    """
    image = source if isinstance(source, PreparedImage) else prepare_image(source)
    run_count = runs if runs is not None else settings.extraction_runs
    chosen_model = model or settings.gemini_model
    key = cache.cache_key(
        image_sha256=image.sha256,
        doc_type=f"{DOC_TYPE}:n{run_count}",
        model=chosen_model,
        prompt_version=PROMPT_VERSION,
    )

    if use_cache:
        cached = cache.load(key)
        if cached is not None:
            try:
                return Prescription.model_validate(cached)
            except ValidationError as exc:
                logger.warning("cache entry %s no longer validates: %s", key[:12], exc)

    dtos = collect_runs(
        dto_type=PrescriptionDTO,
        instruction=PRESCRIPTION_INSTRUCTION,
        image=image,
        doc_type=DOC_TYPE,
        runs=run_count,
        model=model,
    )
    try:
        prescription = build_prescription(dtos)
    except ValueError as exc:
        raise ExtractionError(f"extracted prescription failed domain validation: {exc}") from exc

    if use_cache:
        cache.store(key, prescription.model_dump(mode="json"))
    logger.info(
        "prescription: %d item(s), %d test(s) from %d run(s), counts=%s, %d unstable",
        len(prescription.items), len(prescription.tests), run_count,
        prescription.run_item_counts,
        len(prescription.unstable_lines),
    )
    return prescription


def _cache_key(image: PreparedImage, run_count: int, chosen_model: str) -> str:
    return cache.cache_key(
        image_sha256=image.sha256,
        doc_type=f"{DOC_TYPE}:n{run_count}",
        model=chosen_model,
        prompt_version=PROMPT_VERSION,
    )


async def extract_prescription_async(
    source: Path | bytes | PreparedImage,
    *,
    model: str | None = None,
    use_cache: bool = True,
    runs: int | None = None,
) -> Prescription:
    """Async twin of :func:`extract_prescription`, fanning the N runs out concurrently."""
    image = source if isinstance(source, PreparedImage) else prepare_image(source)
    run_count = runs if runs is not None else settings.extraction_runs
    chosen_model = model or settings.gemini_model
    key = _cache_key(image, run_count, chosen_model)

    if use_cache:
        cached = cache.load(key)
        if cached is not None:
            try:
                return Prescription.model_validate(cached)
            except ValidationError as exc:
                logger.warning("cache entry %s no longer validates: %s", key[:12], exc)

    dtos = await collect_runs_async(
        dto_type=PrescriptionDTO,
        instruction=PRESCRIPTION_INSTRUCTION,
        image=image,
        doc_type=DOC_TYPE,
        runs=run_count,
        model=model,
    )
    try:
        document = build_prescription(dtos)
    except ValueError as exc:
        raise ExtractionError(f"extracted prescription failed domain validation: {exc}") from exc

    if use_cache:
        cache.store(key, document.model_dump(mode="json"))
    return document
