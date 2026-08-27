"""Pharmacy bill extraction: image in, validated :class:`PharmacyBill` out."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

from rxconcile.extract._runner import (
    assign_ids,
    clamp_unit,
    resolve_date,
    run_extraction,
    to_decimal,
)
from rxconcile.extract.dto import PharmacyBillDTO
from rxconcile.extract.errors import ExtractionError
from rxconcile.extract.preprocess import PreparedImage, prepare_image
from rxconcile.extract.prompts import BILL_INSTRUCTION
from rxconcile.models import BilledItem, PharmacyBill

logger: Final = logging.getLogger(__name__)

DOC_TYPE: Final[str] = "bill"
ITEM_ID_PREFIX: Final[str] = "bill"
DEFAULT_CURRENCY: Final[str] = "INR"


def _currency(value: str | None) -> str:
    """Normalise a currency code, falling back to the project default."""
    if value is None:
        return DEFAULT_CURRENCY
    code = value.strip().upper()
    return code if len(code) == 3 and code.isalpha() else DEFAULT_CURRENCY


def to_domain(dto: PharmacyBillDTO) -> PharmacyBill:
    """Convert a model-returned DTO into the strict domain model.

    Identifiers are assigned here in printed order, money becomes ``Decimal``,
    and an ambiguous bill date becomes null plus a warning.
    """
    bill_date, date_warning = resolve_date(dto.bill_date_raw)
    warnings = list(dto.warnings)
    if date_warning:
        warnings.append(date_warning)

    item_ids = assign_ids(ITEM_ID_PREFIX, len(dto.items))
    items = [
        BilledItem(
            item_id=item_id,
            raw_text=source.raw_text,
            drug_name=source.drug_name,
            salt=source.salt,
            strength_value=source.strength_value,
            strength_unit=source.strength_unit,
            form=source.form,
            quantity=source.quantity if (source.quantity or 0) >= 0 else None,
            pack_size=source.pack_size,
            unit_price=to_decimal(source.unit_price),
            line_total=to_decimal(source.line_total),
            batch_no=source.batch_no,
            hsn_code=source.hsn_code,
            confidence=clamp_unit(source.confidence),
        )
        for item_id, source in zip(item_ids, dto.items, strict=True)
    ]

    return PharmacyBill(
        pharmacy_name=dto.pharmacy_name,
        pharmacy_licence_no=dto.pharmacy_licence_no,
        bill_no=dto.bill_no,
        bill_date=bill_date,
        patient_name=dto.patient_name,
        items=items,
        subtotal=to_decimal(dto.subtotal),
        tax_total=to_decimal(dto.tax_total),
        grand_total=to_decimal(dto.grand_total),
        currency=_currency(dto.currency),
        warnings=warnings,
    )


def extract_bill(
    source: Path | bytes | PreparedImage,
    *,
    model: str | None = None,
    use_cache: bool = True,
) -> PharmacyBill:
    """Extract a pharmacy bill from an image.

    Raises:
        ExtractionError: extraction or validation failed. No partial object is
            ever returned.
    """
    image = source if isinstance(source, PreparedImage) else prepare_image(source)
    dto = run_extraction(
        dto_type=PharmacyBillDTO,
        instruction=BILL_INSTRUCTION,
        image=image,
        doc_type=DOC_TYPE,
        model=model,
        use_cache=use_cache,
    )
    try:
        bill = to_domain(dto)
    except ValueError as exc:
        raise ExtractionError(f"extracted bill failed domain validation: {exc}") from exc
    logger.info("bill: %d line(s), grand_total=%s", len(bill.items), bill.grand_total)
    return bill
