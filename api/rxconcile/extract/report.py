"""Lab report extraction.

The third document in the three-way model: the prescription ORDERS a test, the
lab bill CHARGES for it, this PROVES it was performed.

Reports were deliberately not extracted until now, and the reason that decision
was reversible is that nothing had been guessed in the meantime -- the file was
kept with the scan and never read. What made it worth reversing is the middle
axis: without a report, "charged for but never performed" is unaskable.

Hard rule 10 governs every line here. Results are transcribed and never
interpreted, and the schema is built so there is nowhere to record a judgement
even by accident.
"""

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
    collect_runs_async,
    resolve_date,
)
from rxconcile.extract.dto import LabReportDTO
from rxconcile.extract.errors import ExtractionError
from rxconcile.extract.preprocess import PreparedDocument, prepare_document
from rxconcile.extract.prompts import LAB_REPORT_INSTRUCTION, PROMPT_VERSION
from rxconcile.models import LabReport, ReportedTest

logger: Final = logging.getLogger(__name__)

DOC_TYPE: Final[str] = "lab_report"
TEST_ID_PREFIX: Final[str] = "reptest"

#: Fields resolved by cross-run agreement. `result_value` is in here on purpose:
#: a value two runs read as 294.00 and one read as 234.00 is exactly the case
#: agreement exists to catch, and a misread result is the worst thing this
#: document can carry.
TEST_FIELDS: Final[tuple[str, ...]] = (
    "raw_text",
    "bbox",
    "test_name",
    "panel",
    "result_value",
    "unit",
    "reference_range",
    "lab_flag",
    "page",
)

_DOC_FIELDS: Final[tuple[str, ...]] = (
    "lab_name",
    "report_number",
    "patient_name",
    "referred_by",
    "collected_date_raw",
    "reported_date_raw",
    "page_count",
)


def _as_bbox(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, list | tuple) or len(value) != 4:
        return None
    x0, y0, x1, y1 = (float(v) for v in value)
    return (x0, y0, x1, y1)


def _as_page(value: object) -> int | None:
    """A 1-based page number, or null. Never coerced into a guess."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    page = int(value)
    return page if page >= 1 else None


def _build_test(cluster: consensus.ItemCluster, item_id: str) -> ReportedTest:
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

    def text(field: str) -> str | None:
        value = resolved[field].value
        return str(value) if isinstance(value, str) and value.strip() else None

    return ReportedTest(
        item_id=item_id,
        raw_text=cluster.canonical_raw_text,
        bbox=_as_bbox(resolved["bbox"].value),
        test_name=text("test_name"),
        panel=text("panel"),
        # Kept as text, always. See ReportedTest.result_value.
        result_value=text("result_value"),
        unit=text("unit"),
        reference_range=text("reference_range"),
        lab_flag=text("lab_flag"),
        page=_as_page(resolved["page"].value),
        agreement=agreement or None,
        confidence=clamp_unit(sum(confidences) / len(confidences) if confidences else 0.0),
    )


def build_report(runs: list[LabReportDTO]) -> LabReport:
    """Resolve N extraction runs into one report."""
    if not runs:
        raise ExtractionError("no extraction runs to resolve")

    run_count = len(runs)
    clusters = consensus.align_items([list(run.tests) for run in runs])
    kept, unstable = consensus.split_clusters(clusters, run_count=run_count)
    ids = assign_ids(TEST_ID_PREFIX, len(kept))
    tests = [
        _build_test(cluster, item_id) for item_id, cluster in zip(ids, kept, strict=True)
    ]

    doc = {
        field: consensus.resolve_values(
            [getattr(run, field) for run in runs], run_count=run_count
        ).value
        for field in _DOC_FIELDS
    }
    collected = resolve_date(doc["collected_date_raw"])
    reported = resolve_date(doc["reported_date_raw"])

    warnings: list[str] = []
    for run in runs:
        for warning in run.warnings:
            if warning not in warnings:
                warnings.append(warning)
    for resolution in (collected, reported):
        if resolution.warning:
            warnings.append(resolution.warning)

    unnamed = sum(1 for test in tests if test.test_name is None)
    if unnamed:
        warnings.append(
            f"{unnamed} of {len(tests)} result line(s) had no legible test name and "
            "were left null rather than guessed."
        )
    resultless = sum(1 for test in tests if test.result_value is None)
    if resultless:
        warnings.append(
            f"{resultless} result line(s) had no legible value. A test with no result "
            "is not a test that was not performed -- it is one we could not read."
        )
    if unstable:
        warnings.append(
            f"{len(unstable)} result line(s) appeared in some extraction runs but not "
            f"all; counts across runs were {[len(run.tests) for run in runs]}."
        )

    # Union rather than majority: a page ANY run could not read is worth telling
    # the submitter about, and the cost of an unnecessary mention is a second
    # look at a page, while the cost of silence is an unread page nobody knows about.
    unreadable: list[int] = sorted(
        {page for run in runs for page in run.unreadable_pages if page >= 1}
    )

    legibility = [float(run.overall_legibility) for run in runs]
    return LabReport(
        lab_name=doc["lab_name"],
        report_number=doc["report_number"],
        patient_name=doc["patient_name"],
        referred_by=doc["referred_by"],
        collected_date=collected.value,
        reported_date=reported.value,
        tests=tests,
        page_count=_as_page(doc["page_count"]),
        unreadable_pages=unreadable,
        overall_legibility=clamp_unit(sum(legibility) / len(legibility) if legibility else 0.0),
        run_item_counts=[len(run.tests) for run in runs],
        unstable_lines=unstable,
        warnings=warnings,
    )


def _cache_key(document: PreparedDocument, run_count: int, chosen_model: str) -> str:
    return cache.cache_key(
        image_sha256=document.sha256,
        doc_type=f"{DOC_TYPE}:n{run_count}",
        model=chosen_model,
        prompt_version=PROMPT_VERSION,
    )


async def extract_report_async(
    source: Path | bytes | PreparedDocument,
    *,
    model: str | None = None,
    use_cache: bool = True,
    runs: int | None = None,
) -> LabReport:
    """Extract a lab report, resolving N runs by per-field agreement.

    Every page goes in one call -- see `_runner.run_extraction`. A report's
    panel headings span pages, so page-by-page extraction would lose which
    panel a result belongs to.
    """
    document = source if isinstance(source, PreparedDocument) else prepare_document(source)
    run_count = runs if runs is not None else settings.extraction_runs
    chosen_model = model or settings.gemini_model
    key = _cache_key(document, run_count, chosen_model)

    if use_cache:
        cached = cache.load(key)
        if cached is not None:
            try:
                return LabReport.model_validate(cached)
            except ValidationError as exc:
                logger.warning("cache entry %s no longer validates: %s", key[:12], exc)

    dtos = await collect_runs_async(
        dto_type=LabReportDTO,
        instruction=LAB_REPORT_INSTRUCTION,
        document=document,
        doc_type=DOC_TYPE,
        runs=run_count,
        model=model,
    )
    try:
        extracted = build_report(dtos)
    except ValueError as exc:
        raise ExtractionError(f"extracted lab report failed domain validation: {exc}") from exc

    logger.info(
        "lab report: %d test(s) across %s page(s), %d unreadable",
        len(extracted.tests), extracted.page_count, len(extracted.unreadable_pages),
    )
    if use_cache:
        cache.store(key, extracted.model_dump(mode="json"))
    return extracted
