"""Extraction-facing DTOs: what the model is asked to return.

These are deliberately *not* the domain models. They are flat, permissive and
mutable, shaped for what the structured-output endpoint accepts and for what a
vision model can reliably produce. Python converts them into the strict,
frozen domain models, and that conversion is where invariants are imposed.

Three differences matter:

``item_id`` is absent. Identifiers are assigned in Python in document order,
never by the model -- model-generated ids collide or skip, and the domain
uniqueness validator would then reject otherwise-good extractions.
``extra="ignore"`` means an ``item_id`` emitted anyway is silently discarded.

Dates arrive as verbatim strings, not dates. The model transcribes what is
written; Python decides whether it resolves unambiguously. ``03/04/26`` is
genuinely ambiguous and must become ``None`` plus a warning, never a guess.

Money arrives as JSON numbers, not strings. The domain models use ``Decimal``,
whose pydantic string form rejects grouped digits like ``1,200.00`` -- common on
Indian invoices. Asking for a number sidesteps the separator entirely, and
Python converts via ``Decimal(str(value))`` so no binary float error survives.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _DTO(BaseModel):
    """Permissive base: unknown keys are dropped rather than raising."""

    model_config = ConfigDict(extra="ignore")


class PrescribedItemDTO(_DTO):
    """One prescribed line as returned by the model. No item_id."""

    raw_text: str = Field(
        default="",
        description="The line transcribed VERBATIM, with illegible portions marked [?].",
    )
    drug_name: str | None = Field(
        default=None,
        description="Drug name ONLY if confidently legible, else null. Never guess.",
    )
    salt: str | None = Field(default=None, description="Active ingredient if stated, else null.")
    strength_value: float | None = Field(default=None, description="Numeric strength, else null.")
    strength_unit: str | None = Field(
        default=None, description="Unit as written: mg, mcg, ml, IU, %. Else null."
    )
    form: str | None = Field(
        default=None,
        description="tablet, capsule, syrup, injection, ointment, drops, other. Else null.",
    )
    dose_per_administration: float | None = Field(
        default=None, description="Units per administration, else null."
    )
    frequency_raw: str | None = Field(
        default=None, description="Frequency VERBATIM: '1-0-1', 'BD', 'TDS', 'SOS', 'HS'."
    )
    duration_raw: str | None = Field(
        default=None,
        description="Course length EXACTLY as written on the page, in the original "
        "script: 'x 5 days', '5/7', '৪ মাস'. Never convert or normalise it.",
    )
    duration_days: int | None = Field(
        default=None,
        description="ONLY when the page states a plain number of days. Null for weeks, "
        "months, or anything needing conversion — software does that later.",
    )
    route: str | None = Field(default=None, description="oral, topical, IV, etc. Else null.")
    instructions: str | None = Field(default=None, description="Directions, else null.")
    bbox: list[float] | None = Field(
        default=None,
        description="Bounding box of this line as [x0, y0, x1, y1], each normalised "
        "0-1 against the image width and height. Null if you cannot locate the "
        "line on the page. Do not guess a box.",
    )
    confidence: float = Field(
        default=0.0,
        description="Handwriting legibility for THIS line, 0-1. Legibility only, not plausibility.",
    )


class PrescriptionDTO(_DTO):
    """A prescription as returned by the model."""

    patient_name: str | None = Field(default=None)
    patient_age: str | None = Field(
        default=None,
        description="Age VERBATIM WITH ITS UNIT, e.g. '6 months', '34 years', '45Y'. "
        "Never convert to a bare number.",
    )
    patient_sex: str | None = Field(default=None)
    prescriber_name: str | None = Field(default=None)
    prescriber_reg_no: str | None = Field(default=None)
    clinic_name: str | None = Field(default=None)
    date_issued_raw: str | None = Field(
        default=None,
        description="The date EXACTLY as written, e.g. '03/04/26'. Do not reformat or resolve it.",
    )
    diagnosis_text: str | None = Field(default=None)
    items: list[PrescribedItemDTO] = Field(
        default_factory=list, description="Every prescribed line, in document order."
    )
    overall_legibility: float = Field(
        default=0.0, description="Whole-document handwriting legibility, 0-1."
    )
    warnings: list[str] = Field(
        default_factory=list, description="Notable problems, e.g. an illegible signature block."
    )


class BilledItemDTO(_DTO):
    """One billed line as returned by the model. No item_id."""

    raw_text: str = Field(default="", description="The line transcribed VERBATIM.")
    drug_name: str | None = Field(default=None)
    salt: str | None = Field(default=None)
    strength_value: float | None = Field(default=None)
    strength_unit: str | None = Field(default=None)
    form: str | None = Field(
        default=None,
        description="tablet, capsule, syrup, injection, ointment, drops, or 'other' "
        "for non-medicine lines.",
    )
    quantity: float | None = Field(default=None, description="Quantity dispensed.")
    pack_size: str | None = Field(
        default=None,
        description="Pack EXACTLY as printed, e.g. \"10'S\", '15ML'. Do not parse or convert.",
    )
    units_basis: str | None = Field(
        default=None,
        description="Either 'pack' or 'unit', describing what the QTY column counts. "
        "Set ONLY if the bill says so explicitly. Null if it does not. Never guess.",
    )
    unit_price: float | None = Field(default=None, description="Rate or MRP per unit.")
    line_total: float | None = Field(default=None, description="Net amount for this line.")
    batch_no: str | None = Field(default=None)
    hsn_code: str | None = Field(default=None)
    bbox: list[float] | None = Field(
        default=None,
        description="Bounding box of this line as [x0, y0, x1, y1], each normalised "
        "0-1 against the image width and height. Null if you cannot locate the "
        "line on the page. Do not guess a box.",
    )
    confidence: float = Field(default=0.0, description="Legibility for THIS line, 0-1.")


class PharmacyBillDTO(_DTO):
    """A pharmacy bill as returned by the model."""

    pharmacy_name: str | None = Field(default=None)
    pharmacy_licence_no: str | None = Field(default=None)
    bill_no: str | None = Field(default=None)
    bill_date_raw: str | None = Field(
        default=None, description="The bill date EXACTLY as printed. Do not reformat."
    )
    patient_name: str | None = Field(default=None)
    items: list[BilledItemDTO] = Field(
        default_factory=list,
        description="EVERY line on the invoice in printed order, including non-medicine lines.",
    )
    subtotal: float | None = Field(default=None)
    tax_total: float | None = Field(default=None, description="Total GST/tax.")
    grand_total: float | None = Field(default=None, description="Net payable.")
    currency: str | None = Field(default=None, description="ISO code, e.g. INR.")
    warnings: list[str] = Field(default_factory=list)
