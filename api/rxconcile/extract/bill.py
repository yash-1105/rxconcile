"""Pharmacy bill extraction: image in, consensus-resolved :class:`PharmacyBill` out."""

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
    to_decimal,
)
from rxconcile.extract.dto import PharmacyBillDTO
from rxconcile.extract.errors import ExtractionError
from rxconcile.extract.preprocess import PreparedImage, prepare_image
from rxconcile.extract.prompts import BILL_INSTRUCTION, PROMPT_VERSION
from rxconcile.models import BilledItem, PharmacyBill

logger: Final = logging.getLogger(__name__)

DOC_TYPE: Final[str] = "bill"
ITEM_ID_PREFIX: Final[str] = "bill"
DEFAULT_CURRENCY: Final[str] = "INR"

ITEM_FIELDS: Final[tuple[str, ...]] = (
    "raw_text",
    "drug_name",
    "salt",
    "strength_value",
    "strength_unit",
    "form",
    "quantity",
    "pack_size",
    "unit_price",
    "line_total",
    "batch_no",
    "hsn_code",
)

_DOC_FIELDS: Final[tuple[str, ...]] = (
    "pharmacy_name",
    "pharmacy_licence_no",
    "bill_no",
    "bill_date_raw",
    "patient_name",
    "subtotal",
    "tax_total",
    "grand_total",
    "currency",
)


def _currency(value: str | None) -> str:
    """Normalise a currency code, falling back to the project default."""
    if value is None:
        return DEFAULT_CURRENCY
    code = value.strip().upper()
    return code if len(code) == 3 and code.isalpha() else DEFAULT_CURRENCY


def _build_item(cluster: consensus.ItemCluster, item_id: str) -> BilledItem:
    resolved = {field: consensus.resolve_field(cluster, field) for field in ITEM_FIELDS}
    agreement = {
        field: outcome.agreement
        for field, outcome in resolved.items()
        if outcome.agreement is not None
    }
    confidences = [float(getattr(item, "confidence", 0.0)) for item in cluster.present]
    quantity = resolved["quantity"].value
    if isinstance(quantity, int | float) and quantity < 0:
        quantity = None

    return BilledItem(
        item_id=item_id,
        raw_text=cluster.canonical_raw_text,
        drug_name=resolved["drug_name"].value,
        salt=resolved["salt"].value,
        strength_value=resolved["strength_value"].value,
        strength_unit=resolved["strength_unit"].value,
        form=resolved["form"].value,
        quantity=quantity,
        pack_size=resolved["pack_size"].value,
        unit_price=to_decimal(resolved["unit_price"].value),
        line_total=to_decimal(resolved["line_total"].value),
        batch_no=resolved["batch_no"].value,
        hsn_code=resolved["hsn_code"].value,
        agreement=agreement or None,
        confidence=clamp_unit(sum(confidences) / len(confidences) if confidences else 0.0),
    )


def build_bill(runs: list[PharmacyBillDTO]) -> PharmacyBill:
    """Resolve N extraction runs into one bill."""
    if not runs:
        raise ExtractionError("no extraction runs to resolve")

    run_count = len(runs)
    clusters = consensus.align_items([list(run.items) for run in runs])
    kept, unstable = consensus.split_clusters(clusters, run_count=run_count)

    item_ids = assign_ids(ITEM_ID_PREFIX, len(kept))
    items = [_build_item(cluster, item_id) for item_id, cluster in zip(item_ids, kept, strict=True)]

    doc = {
        field: consensus.resolve_values(
            [getattr(run, field) for run in runs], run_count=run_count
        ).value
        for field in _DOC_FIELDS
    }
    bill_date, date_warning = resolve_date(doc["bill_date_raw"])

    warnings: list[str] = []
    for run in runs:
        for warning in run.warnings:
            if warning not in warnings:
                warnings.append(warning)
    if date_warning:
        warnings.append(date_warning)
    if unstable:
        warnings.append(
            f"{len(unstable)} line(s) appeared in some extraction runs but not all; "
            f"item counts across runs were {[len(run.items) for run in runs]}."
        )

    return PharmacyBill(
        pharmacy_name=doc["pharmacy_name"],
        pharmacy_licence_no=doc["pharmacy_licence_no"],
        bill_no=doc["bill_no"],
        bill_date=bill_date,
        patient_name=doc["patient_name"],
        items=items,
        subtotal=to_decimal(doc["subtotal"]),
        tax_total=to_decimal(doc["tax_total"]),
        grand_total=to_decimal(doc["grand_total"]),
        currency=_currency(doc["currency"]),
        run_item_counts=[len(run.items) for run in runs],
        unstable_lines=unstable,
        warnings=warnings,
    )


def extract_bill(
    source: Path | bytes | PreparedImage,
    *,
    model: str | None = None,
    use_cache: bool = True,
    runs: int | None = None,
) -> PharmacyBill:
    """Extract a pharmacy bill, resolving N runs by per-field agreement.

    Raises:
        ExtractionError: extraction or validation failed.
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
                return PharmacyBill.model_validate(cached)
            except ValidationError as exc:
                logger.warning("cache entry %s no longer validates: %s", key[:12], exc)

    dtos = collect_runs(
        dto_type=PharmacyBillDTO,
        instruction=BILL_INSTRUCTION,
        image=image,
        doc_type=DOC_TYPE,
        runs=run_count,
        model=model,
    )
    try:
        bill = build_bill(dtos)
    except ValueError as exc:
        raise ExtractionError(f"extracted bill failed domain validation: {exc}") from exc

    if use_cache:
        cache.store(key, bill.model_dump(mode="json"))
    logger.info(
        "bill: %d line(s) from %d run(s), counts=%s, %d unstable",
        len(bill.items), run_count, bill.run_item_counts, len(bill.unstable_lines),
    )
    return bill


def _cache_key(image: PreparedImage, run_count: int, chosen_model: str) -> str:
    return cache.cache_key(
        image_sha256=image.sha256,
        doc_type=f"{DOC_TYPE}:n{run_count}",
        model=chosen_model,
        prompt_version=PROMPT_VERSION,
    )


async def extract_bill_async(
    source: Path | bytes | PreparedImage,
    *,
    model: str | None = None,
    use_cache: bool = True,
    runs: int | None = None,
) -> PharmacyBill:
    """Async twin of :func:`extract_bill`, fanning the N runs out concurrently."""
    image = source if isinstance(source, PreparedImage) else prepare_image(source)
    run_count = runs if runs is not None else settings.extraction_runs
    chosen_model = model or settings.gemini_model
    key = _cache_key(image, run_count, chosen_model)

    if use_cache:
        cached = cache.load(key)
        if cached is not None:
            try:
                return PharmacyBill.model_validate(cached)
            except ValidationError as exc:
                logger.warning("cache entry %s no longer validates: %s", key[:12], exc)

    dtos = await collect_runs_async(
        dto_type=PharmacyBillDTO,
        instruction=BILL_INSTRUCTION,
        image=image,
        doc_type=DOC_TYPE,
        runs=run_count,
        model=model,
    )
    try:
        document = build_bill(dtos)
    except ValueError as exc:
        raise ExtractionError(f"extracted bill failed domain validation: {exc}") from exc

    if use_cache:
        cache.store(key, document.model_dump(mode="json"))
    return document
