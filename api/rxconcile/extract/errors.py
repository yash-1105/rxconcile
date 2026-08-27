"""Exceptions raised by the extraction layer."""

from __future__ import annotations


class ExtractionError(RuntimeError):
    """Extraction failed and produced no usable object.

    Raised rather than returning a partially populated result: a half-filled
    prescription is indistinguishable from a real one downstream, and this
    system must never present fabricated content as extracted content.
    """


class ImageTooLargeError(ExtractionError):
    """The supplied image exceeds ``MAX_UPLOAD_MB``."""


class UnreadableImageError(ExtractionError):
    """The supplied bytes could not be decoded as an image."""
