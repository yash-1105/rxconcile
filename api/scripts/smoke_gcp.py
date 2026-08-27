#!/usr/bin/env python
"""Prove the Vertex chain works through the google-genai SDK.

The SDK equivalent of ``api/scripts/verify_vertex.sh``, which does the same
checks with raw curl. Running both confirms the SDK layer -- config, ADC client,
retry wrapper -- behaves like the transport underneath it.

Checks, in order:

1. every configured model resolves against this project (boot assertion)
2. a text call returns exactly "OK"
3. a generated PNG is transcribed from inline bytes (proves multimodal)
4. the health snapshot reports which model served

Exit code is 0 only if all four pass.
"""

from __future__ import annotations

import io
import logging
import sys
from typing import Final

from google.genai import types
from PIL import Image, ImageDraw, ImageFont

from rxconcile.config import settings
from rxconcile.gcp import assert_models_resolve, generate_content, health_snapshot
from rxconcile.gcp.errors import GcpError

EXPECTED_TEXT: Final[str] = "PARACETAMOL 500"

_FONT_CANDIDATES: Final[tuple[str, ...]] = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_png(text: str) -> bytes:
    """Render ``text`` as black-on-white PNG bytes, sized to fit the glyphs."""
    font = _load_font(76)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), text, font=font)
    pad = 50
    width = int(right - left) + 2 * pad
    height = int(bottom - top) + 2 * pad
    image = Image.new("RGB", (width, height), "white")
    ImageDraw.Draw(image).text((pad - left, pad - top), text, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def check_models_resolve() -> bool:
    print("[1/4] model resolution")
    try:
        verified = assert_models_resolve()
    except GcpError as exc:
        print(f"      FAIL: {exc}")
        return False
    for model in verified:
        print(f"      resolves: {model}")
    return True


def check_text() -> bool:
    print("[2/4] text generation")
    result = generate_content("Reply with exactly: OK")
    text = result.text.strip()
    print(f"      model={result.model} attempts={result.attempts} text={text!r}")
    if text != "OK":
        print(f"      FAIL: expected 'OK', got {text!r}")
        return False
    return True


def check_multimodal() -> bool:
    print("[3/4] multimodal transcription")
    png = render_png(EXPECTED_TEXT)
    print(f"      generated PNG: {len(png)} bytes")
    parts: list[types.PartUnionDict] = [
        types.Part.from_bytes(data=png, mime_type="image/png"),
        types.Part.from_text(
            text=(
                "Transcribe the text visible in this image exactly. "
                "Output only the transcribed text."
            )
        ),
    ]
    result = generate_content(parts)
    text = result.text.strip()
    modalities = [
        detail.modality.name if detail.modality else "?"
        for detail in (result.response.usage_metadata.prompt_tokens_details or [])
    ] if result.response.usage_metadata else []
    print(f"      model={result.model} transcription={text!r}")
    print(f"      prompt modalities={modalities}")
    if EXPECTED_TEXT not in text:
        print(f"      FAIL: expected {EXPECTED_TEXT!r} in transcription")
        return False
    if "IMAGE" not in modalities:
        print("      FAIL: no IMAGE modality reported; image was not actually read")
        return False
    return True


def check_health() -> bool:
    print("[4/4] health snapshot")
    snapshot = health_snapshot()
    for key, value in snapshot.model_dump().items():
        print(f"      {key}: {value}")
    if not snapshot.healthy:
        print("      FAIL: snapshot reports unhealthy")
        return False
    if snapshot.last_served_model is None:
        print("      FAIL: no model recorded as having served a request")
        return False
    return True


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="      log: %(name)s %(message)s")
    print(
        f"project={settings.gcp_project_id} location={settings.gcp_location} "
        f"model={settings.gemini_model}"
    )
    checks = (check_models_resolve, check_text, check_multimodal, check_health)
    for check in checks:
        if not check():
            print("\nFAILED")
            return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
