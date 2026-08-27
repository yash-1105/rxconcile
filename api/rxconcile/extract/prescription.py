"""Prescription extraction: image in, validated :class:`Prescription` out."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

from rxconcile.extract._runner import (
    assign_ids,
    clamp_unit,
    resolve_date,
    run_extraction,
)
from rxconcile.extract.dto import PrescriptionDTO
from rxconcile.extract.errors import ExtractionError
from rxconcile.extract.preprocess import PreparedImage, prepare_image
from rxconcile.extract.prompts import PRESCRIPTION_INSTRUCTION
from rxconcile.models import PrescribedItem, Prescription

logger: Final = logging.getLogger(__name__)

DOC_TYPE: Final[str] = "prescription"
ITEM_ID_PREFIX: Final[str] = "rx"


def to_domain(dto: PrescriptionDTO) -> Prescription:
    """Convert a model-returned DTO into the strict domain model.

    Python owns three things the model is never asked to decide: item
    identifiers (assigned in document order), date resolution (ambiguous dates
    become null plus a warning), and score clamping.
    """
    date_issued, date_warning = resolve_date(dto.date_issued_raw)
    warnings = list(dto.warnings)
    if date_warning:
        warnings.append(date_warning)

    item_ids = assign_ids(ITEM_ID_PREFIX, len(dto.items))
    items = [
        PrescribedItem(
            item_id=item_id,
            raw_text=source.raw_text,
            drug_name=source.drug_name,
            salt=source.salt,
            strength_value=source.strength_value,
            strength_unit=source.strength_unit,
            form=source.form,
            dose_per_administration=source.dose_per_administration,
            frequency_raw=source.frequency_raw,
            duration_days=source.duration_days if (source.duration_days or 0) >= 0 else None,
            route=source.route,
            instructions=source.instructions,
            confidence=clamp_unit(source.confidence),
        )
        for item_id, source in zip(item_ids, dto.items, strict=True)
    ]

    unnamed = sum(1 for item in items if item.drug_name is None)
    if unnamed:
        warnings.append(
            f"{unnamed} of {len(items)} prescribed item(s) had no legible drug name "
            "and were left null rather than guessed."
        )

    return Prescription(
        patient_name=dto.patient_name,
        patient_age=dto.patient_age,
        patient_sex=dto.patient_sex,
        prescriber_name=dto.prescriber_name,
        prescriber_reg_no=dto.prescriber_reg_no,
        clinic_name=dto.clinic_name,
        date_issued=date_issued,
        diagnosis_text=dto.diagnosis_text,
        items=items,
        overall_legibility=clamp_unit(dto.overall_legibility),
        warnings=warnings,
    )


def extract_prescription(
    source: Path | bytes | PreparedImage,
    *,
    model: str | None = None,
    use_cache: bool = True,
) -> Prescription:
    """Extract a prescription from an image.

    Args:
        source: image path, raw bytes, or an already-prepared image.
        model: override the configured extraction model.
        use_cache: read and write the on-disk extraction cache.

    Raises:
        ExtractionError: extraction or validation failed. No partial object is
            ever returned.
    """
    image = source if isinstance(source, PreparedImage) else prepare_image(source)
    dto = run_extraction(
        dto_type=PrescriptionDTO,
        instruction=PRESCRIPTION_INSTRUCTION,
        image=image,
        doc_type=DOC_TYPE,
        model=model,
        use_cache=use_cache,
    )
    try:
        prescription = to_domain(dto)
    except ValueError as exc:
        raise ExtractionError(
            f"extracted prescription failed domain validation: {exc}"
        ) from exc
    logger.info(
        "prescription: %d item(s), overall_legibility=%.2f",
        len(prescription.items), prescription.overall_legibility,
    )
    return prescription
