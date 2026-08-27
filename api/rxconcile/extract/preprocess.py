"""Image normalisation before extraction.

Phone photographs of prescriptions arrive rotated (EXIF orientation), far larger
than the model can use, and occasionally too large to accept at all. This module
makes them uniform: upright, bounded, and JPEG-encoded, with a content hash for
caching.
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
from rxconcile.extract.errors import ImageTooLargeError, UnreadableImageError

logger: Final = logging.getLogger(__name__)

#: Longest edge, in pixels, after downscaling. Larger buys no accuracy and costs
#: tokens; smaller starts losing thin handwriting strokes.
MAX_EDGE_PX: Final[int] = 2000

#: JPEG quality for the encoded result.
JPEG_QUALITY: Final[int] = 90

OUTPUT_MIME_TYPE: Final[str] = "image/jpeg"


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
    raw = _read(source)
    if not raw:
        raise UnreadableImageError("image is empty")

    limit = settings.max_upload_bytes
    if len(raw) > limit:
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
