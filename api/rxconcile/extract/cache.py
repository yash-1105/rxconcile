"""Disk cache for extraction results.

Extraction is the expensive step, and a demo reloads the same two images
repeatedly. Results are cached as JSON under ``.cache/`` (gitignored).

The key is the sha256 of the original image bytes **combined with the document
type, model ID and prompt version**. Keying on image bytes alone would serve
stale results the moment a prompt is tuned, which defeats the workflow the cache
exists to support -- iterating on prompts against a fixed image.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Final

from rxconcile.config import settings

logger: Final = logging.getLogger(__name__)

CACHE_DIR: Final[Path] = Path(__file__).resolve().parents[3] / ".cache" / "extraction"


def cache_key(
    *, image_sha256: str, doc_type: str, model: str, prompt_version: str
) -> str:
    """Derive a cache key that changes when the resolved output would change.

    The cache holds the RESOLVED document, not the raw model reply, so anything
    that changes resolution has to be in the key. ``date_order`` decides whether
    an ambiguous date becomes a value or a null, so a cached document read under
    one convention must not be served under another.
    """
    material = (
        f"{image_sha256}|{doc_type}|{model}|{prompt_version}|{settings.date_order}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _path_for(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def load(key: str) -> dict[str, Any] | None:
    """Return the cached payload for ``key``, or None on any miss.

    A corrupt entry is treated as a miss rather than an error: a bad cache file
    should never break extraction.
    """
    path = _path_for(key)
    if not path.is_file():
        return None
    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("ignoring unreadable cache entry %s: %s", path.name, exc)
        return None
    logger.info("cache hit %s", key[:12])
    return payload


def store(key: str, payload: dict[str, Any]) -> None:
    """Persist ``payload``. Cache failures are logged, never raised."""
    path = _path_for(key)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)  # atomic, so a crash cannot leave a partial entry
        logger.info("cached %s", key[:12])
    except OSError as exc:
        logger.warning("could not write cache entry %s: %s", path.name, exc)


def clear() -> int:
    """Delete every cache entry. Returns how many were removed."""
    if not CACHE_DIR.is_dir():
        return 0
    removed = 0
    for entry in CACHE_DIR.glob("*.json"):
        try:
            entry.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("could not remove %s: %s", entry.name, exc)
    return removed
