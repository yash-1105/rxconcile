"""Gemini-backed extraction: images in, validated domain models out.

The model performs EXTRACTION ONLY. It never compares documents and never
decides whether they agree; that is the reconciliation engine's job.
"""

from rxconcile.extract.bill import extract_bill
from rxconcile.extract.errors import (
    ExtractionError,
    ImageTooLargeError,
    UnreadableImageError,
)
from rxconcile.extract.preprocess import PreparedImage, prepare_image
from rxconcile.extract.prescription import extract_prescription

__all__ = [
    "ExtractionError",
    "ImageTooLargeError",
    "PreparedImage",
    "UnreadableImageError",
    "extract_bill",
    "extract_prescription",
    "prepare_image",
]
