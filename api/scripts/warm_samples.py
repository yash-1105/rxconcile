"""Pre-compute the extraction cache for every bundled sample.

Selecting a sample should return instantly. It already can: extraction results
are cached on disk under ``.cache/extraction``, keyed by image hash, document
type, model and prompt version. The cache is simply cold on a fresh clone, and
on the first run after a prompt change -- which is when a demo is most likely to
be sitting in front of someone.

This does NOT reduce the number of model calls a real upload makes. N=3 stays
the default everywhere: it is the agreement measurement that every reliability
claim in this product rests on, and it costs little wall-clock because the three
runs fan out concurrently. This only means the *bundled samples* are already
computed by the time anyone clicks one.

Run it after changing PROMPT_VERSION, or after cloning:

    make warm
"""

from __future__ import annotations

import asyncio
import time

from rxconcile.config import samples_dir, settings
from rxconcile.extract.bill import extract_bill_async
from rxconcile.extract.prescription import extract_prescription_async

SAMPLES_DIR = samples_dir()

#: Mirrors the SAMPLES table in main.py. Kept as plain data so warming needs no
#: running server.
PAIRS: tuple[tuple[str, str, str], ...] = (
    ("sample-01", "sample-01-prescription.png", "sample-01-bill.png"),
    ("sample-02", "sample-02-prescription.png", "sample-02-bill.png"),
    ("sample-03", "sample-03-prescription.png", "sample-03-bill.png"),
    ("p3-dental", "p3.jpg", "synthetic_bill_p3.png"),
    ("synthetic-clean", "synthetic_prescription.png", "synthetic_bill.png"),
)


async def warm_one(sample_id: str, prescription: str, bill: str) -> tuple[str, float, str]:
    started = time.monotonic()
    try:
        await asyncio.gather(
            extract_prescription_async(SAMPLES_DIR / prescription),
            extract_bill_async(SAMPLES_DIR / bill),
        )
    except Exception as exc:  # noqa: BLE001 - a warm failure must not be fatal
        return sample_id, time.monotonic() - started, f"FAILED {type(exc).__name__}: {exc}"
    return sample_id, time.monotonic() - started, "ok"


async def main() -> int:
    print(f"warming {len(PAIRS)} sample(s) at N={settings.extraction_runs}\n")
    failures = 0
    for sample_id, prescription, bill in PAIRS:
        name, elapsed, status = await warm_one(sample_id, prescription, bill)
        marker = "  " if status == "ok" else "! "
        print(f"{marker}{name:18} {elapsed:6.1f}s  {status}")
        if status != "ok":
            failures += 1
    print("\nSamples are cached. Selecting one now returns without a model call.")
    print("Real uploads still run at N=3.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
