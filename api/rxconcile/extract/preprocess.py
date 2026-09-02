"""Document normalisation before extraction.

Phone photographs of prescriptions arrive rotated (EXIF orientation), far larger
than the model can use, and occasionally too large to accept at all. This module
makes them uniform: upright, bounded, and JPEG-encoded, with a content hash for
caching.

A document is a SEQUENCE of pages, not one image. PDFs used to have page 1
rendered and every other page silently discarded -- fine for a one-page bill,
and quietly wrong for a two-page prescription or a six-page lab report, with no
error anywhere to say half the document was never read. `prepare_document`
renders all of them, and refuses outright past `MAX_PDF_PAGES` rather than
truncating, because a truncated document looks exactly like a complete one.
"""

from __future__ import annotations

import hashlib
import io
import logging
from pathlib import Path
from typing import Final

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from rxconcile.config import settings
from rxconcile.extract.errors import (
    ImageTooLargeError,
    TooManyPagesError,
    UnreadableImageError,
)

logger: Final = logging.getLogger(__name__)

#: Longest edge, in pixels, after downscaling. Larger buys no accuracy and costs
#: tokens; smaller starts losing thin handwriting strokes.
MAX_EDGE_PX: Final[int] = 2000

#: JPEG quality for the encoded result.
JPEG_QUALITY: Final[int] = 90

OUTPUT_MIME_TYPE: Final[str] = "image/jpeg"

PDF_MIME_TYPE: Final[str] = "application/pdf"

#: 200 DPI keeps thin handwriting strokes legible without bloating the payload.
PDF_RENDER_DPI: Final[int] = 200

#: Most pages one document may contribute. A real lab report runs to six; a
#: scanned booklet runs to hundreds and would cost a fortune per extraction run,
#: three times over. Beyond this the upload is REFUSED, never trimmed.
MAX_PDF_PAGES: Final[int] = 15


class PreparedImage(BaseModel):
    """A normalised image, ready to send inline."""

    model_config = ConfigDict(frozen=True)

    data: bytes = Field(description="JPEG bytes to send to the model.")
    mime_type: str = Field(default=OUTPUT_MIME_TYPE)
    sha256: str = Field(description="Hex digest of the ORIGINAL bytes; the cache key.")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    original_bytes: int = Field(gt=0)

    @property
    def encoded_bytes(self) -> int:
        return len(self.data)


def _read(source: Path | bytes) -> bytes:
    if isinstance(source, bytes):
        return source
    try:
        return source.read_bytes()
    except OSError as exc:
        raise UnreadableImageError(f"could not read {source}: {exc}") from exc


def prepare_image(source: Path | bytes) -> PreparedImage:
    """Normalise ``source`` into an upright, bounded JPEG.

    Steps: reject oversized input, decode, apply EXIF orientation, drop alpha,
    downscale the longest edge to :data:`MAX_EDGE_PX`, re-encode as JPEG.

    The hash is taken over the ORIGINAL bytes, so the cache key is stable even
    if the preprocessing parameters change. Prompt and model identity are mixed
    into the cache key separately.

    Raises:
        ImageTooLargeError: input exceeds ``MAX_UPLOAD_MB``.
        UnreadableImageError: bytes are not a decodable image.
    """
    return _normalise(_read(source), check_upload_limit=True)


def _normalise(raw: bytes, *, check_upload_limit: bool) -> PreparedImage:
    """The image pipeline itself.

    `check_upload_limit` is False for a page rendered out of a PDF: the limit
    governs what a person UPLOADED, and a rendered page is something this code
    produced. Applying it there would reject a perfectly small PDF because one
    of its pages rasterised large.
    """
    if not raw:
        raise UnreadableImageError("image is empty")

    if check_upload_limit and len(raw) > settings.max_upload_bytes:
        raise ImageTooLargeError(
            f"image is {len(raw) / 1_048_576:.1f} MB, limit is {settings.max_upload_mb} MB "
            f"(MAX_UPLOAD_MB). Reduce the image and retry."
        )

    digest = hashlib.sha256(raw).hexdigest()

    try:
        with Image.open(io.BytesIO(raw)) as image:
            # Honour EXIF orientation, then discard the tag so it cannot be
            # applied a second time downstream.
            upright = ImageOps.exif_transpose(image)
            if upright is None:
                upright = image
            upright = upright.convert("RGB")

            longest = max(upright.size)
            if longest > MAX_EDGE_PX:
                scale = MAX_EDGE_PX / longest
                target = (
                    max(1, round(upright.width * scale)),
                    max(1, round(upright.height * scale)),
                )
                upright = upright.resize(target, Image.Resampling.LANCZOS)

            buffer = io.BytesIO()
            upright.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            width, height = upright.size
    except UnidentifiedImageError as exc:
        raise UnreadableImageError(
            "bytes could not be decoded as an image; expected JPEG, PNG, HEIC or similar"
        ) from exc
    except OSError as exc:
        raise UnreadableImageError(f"image could not be processed: {exc}") from exc

    encoded = buffer.getvalue()
    logger.info(
        "prepared image %s: %d bytes -> %dx%d, %d bytes jpeg",
        digest[:12], len(raw), width, height, len(encoded),
    )
    return PreparedImage(
        data=encoded,
        sha256=digest,
        width=width,
        height=height,
        original_bytes=len(raw),
    )


class PreparedDocument(BaseModel):
    """One uploaded document, as an ordered sequence of prepared pages.

    A single image is a one-page document, so every caller can treat images and
    PDFs identically instead of branching on type.
    """

    model_config = ConfigDict(frozen=True)

    pages: tuple[PreparedImage, ...] = Field(min_length=1)
    sha256: str = Field(
        description="Hex digest of the ORIGINAL uploaded bytes -- the whole PDF, "
        "not any one page. The cache key, so it must not change when render "
        "settings do."
    )
    source_mime: str = Field(description="What was uploaded, before rendering.")

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def first(self) -> PreparedImage:
        """The first page.

        For the handful of places that legitimately want one image -- a
        thumbnail, a bbox highlight that has only ever referenced page 1.
        Extraction must NOT use this: that is the bug being fixed.
        """
        return self.pages[0]

    @property
    def encoded_bytes(self) -> int:
        return sum(page.encoded_bytes for page in self.pages)


def _render_pdf(raw: bytes) -> list[bytes]:
    """Every page of a PDF, as PNG bytes, in order."""
    import pypdfium2

    try:
        document = pypdfium2.PdfDocument(io.BytesIO(raw))
        count = len(document)
    except Exception as exc:  # pypdfium2 raises assorted native errors
        raise UnreadableImageError(f"PDF could not be opened: {exc}") from exc

    if count == 0:
        raise UnreadableImageError("PDF contains no pages")
    if count > MAX_PDF_PAGES:
        # Refused, not trimmed. See MAX_PDF_PAGES.
        raise TooManyPagesError(
            f"This PDF has {count} pages and the limit is {MAX_PDF_PAGES}. "
            "Split it, or upload only the pages that belong to this claim. "
            "Nothing was read: the whole document is rejected rather than "
            "quietly cut short at page "
            f"{MAX_PDF_PAGES}."
        )

    rendered: list[bytes] = []
    try:
        for index in range(count):
            image = document[index].render(scale=PDF_RENDER_DPI / 72).to_pil()
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            rendered.append(buffer.getvalue())
    except Exception as exc:
        raise UnreadableImageError(
            f"PDF page {len(rendered) + 1} of {count} could not be rendered: {exc}"
        ) from exc
    return rendered


def prepare_document(
    source: Path | bytes, *, content_type: str | None = None
) -> PreparedDocument:
    """Normalise an upload into an ordered list of pages ready to send.

    A PDF becomes one prepared page per PDF page; anything else becomes a
    single-page document. The upload size limit is applied ONCE, to what was
    actually uploaded.

    Raises:
        ImageTooLargeError: the upload exceeds ``MAX_UPLOAD_MB``.
        TooManyPagesError: the PDF has more than ``MAX_PDF_PAGES`` pages.
        UnreadableImageError: the bytes are not a decodable image or PDF.
    """
    raw = _read(source)
    if not raw:
        raise UnreadableImageError("document is empty")
    if len(raw) > settings.max_upload_bytes:
        raise ImageTooLargeError(
            f"file is {len(raw) / 1_048_576:.1f} MB, limit is {settings.max_upload_mb} MB "
            f"(MAX_UPLOAD_MB). Reduce the file and retry."
        )

    digest = hashlib.sha256(raw).hexdigest()
    mime = (content_type or "").split(";")[0].strip().lower()
    is_pdf = mime == PDF_MIME_TYPE or raw[:5] == b"%PDF-"

    if is_pdf:
        pages = tuple(
            _normalise(page, check_upload_limit=False) for page in _render_pdf(raw)
        )
        logger.info("prepared PDF %s: %d page(s)", digest[:12], len(pages))
    else:
        pages = (_normalise(raw, check_upload_limit=False),)

    return PreparedDocument(
        pages=pages,
        sha256=digest,
        source_mime=mime or (PDF_MIME_TYPE if is_pdf else OUTPUT_MIME_TYPE),
    )
