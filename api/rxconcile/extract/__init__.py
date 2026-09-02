"""Gemini-backed extraction: images in, validated domain models out.

The model performs EXTRACTION ONLY. It never compares documents and never
decides whether they agree; that is the reconciliation engine's job.
"""

from rxconcile.extract.bill import build_bill, extract_bill, extract_bill_async
from rxconcile.extract.errors import (
    ExtractionError,
    ImageTooLargeError,
    UnreadableImageError,
)
from rxconcile.extract.preprocess import PreparedImage, prepare_image
from rxconcile.extract.prescription import (
    build_prescription,
    extract_prescription,
    extract_prescription_async,
)
from rxconcile.extract.report import extract_report_async

__all__ = [
    "extract_report_async",
    "ExtractionError",
    "build_bill",
    "build_prescription",
    "ImageTooLargeError",
    "PreparedImage",
    "UnreadableImageError",
    "extract_bill",
    "extract_bill_async",
    "extract_prescription",
    "extract_prescription_async",
    "prepare_image",
]
