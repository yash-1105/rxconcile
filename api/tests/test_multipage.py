"""Every page of a document reaches the model, or the upload is refused.

The bug this replaces: a PDF had page 1 rendered and pages 2..N dropped, with
nothing anywhere recording the loss. A one-page bill was fine; a two-page
prescription or a six-page lab report was read a fraction and reported as if it
had been read whole -- which is worse than rejecting the file, because the
result looks complete.
"""

from __future__ import annotations

import io
from typing import Any

import pypdfium2
import pytest
from PIL import Image

from rxconcile.extract import _runner, preprocess
from rxconcile.extract.errors import TooManyPagesError, UnreadableImageError
from rxconcile.extract.preprocess import MAX_PDF_PAGES, prepare_document


def pdf_bytes(pages: int) -> bytes:
    """A PDF with `pages` blank A4 pages."""
    doc = pypdfium2.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(595, 842)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def png_bytes(size: tuple[int, int] = (600, 800)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


class TestEveryPageIsRendered:
    @pytest.mark.parametrize("count", [1, 2, 6, MAX_PDF_PAGES])
    def test_page_for_page(self, count: int) -> None:
        document = prepare_document(pdf_bytes(count))
        assert document.page_count == count
        assert len(document.pages) == count

    def test_an_image_is_a_one_page_document(self) -> None:
        """So callers never branch on type."""
        assert prepare_document(png_bytes()).page_count == 1

    def test_pages_are_prepared_the_same_way_an_image_is(self) -> None:
        """Same normalisation, so a bbox means the same thing on any page."""
        document = prepare_document(pdf_bytes(3))
        for page in document.pages:
            assert page.mime_type == preprocess.OUTPUT_MIME_TYPE
            assert max(page.width, page.height) <= preprocess.MAX_EDGE_PX
            assert page.data[:2] == b"\xff\xd8"  # JPEG SOI

    def test_the_hash_is_of_the_whole_file_not_a_page(self) -> None:
        """The cache key must change when ANY page changes."""
        import hashlib

        raw = pdf_bytes(4)
        assert prepare_document(raw).sha256 == hashlib.sha256(raw).hexdigest()
        assert prepare_document(pdf_bytes(4)).sha256 != prepare_document(pdf_bytes(5)).sha256


class TestThePageCapRefusesRatherThanTruncates:
    def test_over_the_cap_raises(self) -> None:
        with pytest.raises(TooManyPagesError) as caught:
            prepare_document(pdf_bytes(MAX_PDF_PAGES + 1))
        message = str(caught.value)
        # The message must say what happened, the limit, and what to do.
        assert str(MAX_PDF_PAGES + 1) in message
        assert str(MAX_PDF_PAGES) in message
        assert "Nothing was read" in message

    def test_exactly_at_the_cap_is_accepted(self) -> None:
        assert prepare_document(pdf_bytes(MAX_PDF_PAGES)).page_count == MAX_PDF_PAGES

    def test_a_pdf_with_nothing_in_it_is_rejected_not_treated_as_read(self) -> None:
        """The wording is not pinned, the refusal is.

        A zero-page PDF cannot even be loaded, so this trips the open guard
        rather than the page-count one. Either way nothing is returned, which
        is the property that matters: no caller receives an empty document it
        might mistake for a read one.
        """
        with pytest.raises(UnreadableImageError):
            prepare_document(pdf_bytes(0))


class TestOneCallCarriesEveryPage:
    """A panel heading on page 4 governs a result on page 5.

    Extracting page by page and stitching cannot preserve that, so the pages
    must arrive in a single request.
    """

    def test_every_page_becomes_a_part_before_the_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        document = prepare_document(pdf_bytes(6))
        seen: dict[str, Any] = {}

        class _Result:
            text = '{"items": [], "tests": [], "overall_legibility": 0.7, "warnings": []}'
            model = "test-model"

        def fake_generate(contents: list[Any], **_: Any) -> Any:
            seen["parts"] = contents
            return _Result()

        monkeypatch.setattr(_runner, "generate_content", fake_generate)

        from rxconcile.extract.dto import PrescriptionDTO

        _runner.run_extraction(
            dto_type=PrescriptionDTO,
            instruction="I",
            document=document,
            doc_type="prescription",
            use_cache=False,
        )
        parts = seen["parts"]
        # Six image parts then the instruction: one call, whole document.
        assert len(parts) == document.page_count + 1
        image_parts = parts[: document.page_count]
        assert all(getattr(p, "inline_data", None) is not None for p in image_parts)
        assert getattr(parts[-1], "text", None) == "I"

    def test_page_order_is_preserved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Page 5 must not arrive before page 4."""
        document = prepare_document(pdf_bytes(4))
        seen: dict[str, Any] = {}

        class _Result:
            text = '{"items": [], "tests": [], "overall_legibility": 0.7, "warnings": []}'
            model = "m"

        monkeypatch.setattr(
            _runner, "generate_content",
            lambda contents, **_: (seen.update(parts=contents), _Result())[1],
        )
        from rxconcile.extract.dto import PrescriptionDTO

        _runner.run_extraction(
            dto_type=PrescriptionDTO, instruction="I", document=document,
            doc_type="prescription", use_cache=False,
        )
        sent = [p.inline_data.data for p in seen["parts"][:-1]]
        assert sent == [page.data for page in document.pages]


def test_the_upload_limit_governs_the_upload_not_a_rendered_page() -> None:
    """A small PDF whose pages rasterise large must not be rejected.

    The limit exists to stop somebody uploading a huge file. A page this code
    rendered was never uploaded, so applying the limit to it would reject a
    perfectly reasonable document for something the reader did not do.
    """
    raw = pdf_bytes(6)
    document = prepare_document(raw)
    assert len(raw) < document.encoded_bytes, (
        "fixture must actually expand on render for this to prove anything"
    )
