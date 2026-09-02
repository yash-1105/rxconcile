#!/usr/bin/env python
"""Extract one document and print it as JSON. The prompt-iteration loop.

    python api/scripts/extract_one.py samples/rx1.jpg --type prescription
    python api/scripts/extract_one.py samples/bill1.jpg --type bill --no-cache

The cache key includes the prompt version, so editing a prompt in prompts.py and
bumping PROMPT_VERSION invalidates prior results automatically. Use --no-cache
to force a fresh call without touching the version.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rxconcile.config import settings
from rxconcile.extract import extract_bill, extract_prescription
from rxconcile.extract.cache import clear as clear_cache
from rxconcile.extract.errors import ExtractionError
from rxconcile.extract.preprocess import prepare_document
from rxconcile.models import PharmacyBill, Prescription


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract a prescription or pharmacy bill from an image.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("image", type=Path, nargs="?", help="Path to the image file.")
    parser.add_argument(
        "--type",
        dest="doc_type",
        choices=("prescription", "bill"),
        help="Which document this is.",
    )
    parser.add_argument("--model", default=None, help="Override the configured model.")
    parser.add_argument(
        "--runs",
        type=int,
        default=None,
        help="Extraction runs to resolve by agreement (default EXTRACTION_RUNS).",
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="Bypass the on-disk extraction cache."
    )
    parser.add_argument(
        "--clear-cache", action="store_true", help="Delete all cached extractions and exit."
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Log progress to stderr.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.clear_cache:
        print(f"removed {clear_cache()} cache entries", file=sys.stderr)
        return 0

    if args.image is None or args.doc_type is None:
        build_parser().error("image and --type are required unless --clear-cache is used")

    if not args.image.is_file():
        print(f"error: no such file: {args.image}", file=sys.stderr)
        return 2

    model = args.model or settings.gemini_model
    runs = args.runs if args.runs is not None else settings.extraction_runs
    print(
        f"extracting {args.doc_type} from {args.image} using {model}, {runs} run(s)",
        file=sys.stderr,
    )

    try:
        # A PDF here now contributes every page, not just its first.
        image = prepare_document(args.image)
        first = image.first
        print(
            f"prepared: {image.page_count} page(s), "
            f"first {first.width}x{first.height}, "
            f"{first.original_bytes:,} -> {image.encoded_bytes:,} bytes, "
            f"sha256={image.sha256[:12]}",
            file=sys.stderr,
        )
        document: Prescription | PharmacyBill = (
            extract_prescription(
                image, model=args.model, use_cache=not args.no_cache, runs=args.runs
            )
            if args.doc_type == "prescription"
            else extract_bill(
                image, model=args.model, use_cache=not args.no_cache, runs=args.runs
            )
        )
    except ExtractionError as exc:
        print(f"extraction failed: {exc}", file=sys.stderr)
        return 1

    print(document.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
