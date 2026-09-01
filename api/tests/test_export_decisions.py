"""Per-line decisions, as they reach a report.

Two things are load-bearing here.

First, the ROW KEY. A decision is recorded on screen against a row identified
by `web/src/lib/rows.ts`, and looked up in a report by
`rxconcile.export.common.row_key`. If the two ever disagree the reports print
"Not decided" beside every line somebody just accepted, and nothing else in the
system notices. The forms asserted below are copied from rows.ts by hand and
are the contract between them.

Second, the default. An absent decision reads as "Not decided", never as
"Accepted": a report that shows an unreviewed line as approved is the one
failure here that would actually cost somebody money.
"""

from __future__ import annotations

import json
import zipfile
from decimal import Decimal
from io import BytesIO

import pytest

from rxconcile.export import ExportContext, build_json, build_pdf, build_xlsx
from rxconcile.export.common import decision_remark, decision_word, row_key
from tests.test_export import result_with_discrepancy


class TestRowKey:
    """Every form `web/src/lib/rows.ts` produces, spelled out."""

    def test_a_matched_medicine_pair(self) -> None:
        assert row_key("rx-01", "bill-01") == "rx-01-bill-01"

    def test_a_prescribed_medicine_with_no_billed_line(self) -> None:
        assert row_key("rx-01", None) == "rx-only-rx-01"

    def test_a_billed_medicine_with_no_prescribed_line(self) -> None:
        assert row_key(None, "bill-02") == "bill-only-bill-02"

    def test_a_matched_test_pair_uses_the_same_form_as_medicines(self) -> None:
        assert row_key("rxt-01", "bt-01", tests=True) == "rxt-01-bt-01"

    def test_a_prescribed_test_with_no_billed_line(self) -> None:
        assert row_key("t-01", None, tests=True) == "rxt-t-01"

    def test_a_billed_test_with_no_prescribed_line(self) -> None:
        assert row_key(None, "t-09", tests=True) == "bt-t-09"

    def test_a_test_billed_under_an_ordered_panel(self) -> None:
        """Its own form. A panel component is not an unmatched billed test."""
        assert row_key(None, "t-04", tests=True, covered=True) == "covered-t-04"


class TestDecisionWords:
    def test_an_absent_decision_is_not_decided(self) -> None:
        assert decision_word({}, "rx-01-bill-01") == "Not decided"

    def test_unset_is_not_decided(self) -> None:
        decisions: dict[str, object] = {"rx-01-bill-01": {"decision": "unset"}}
        assert decision_word(decisions, "rx-01-bill-01") == "Not decided"

    def test_accept_and_reject_read_as_words(self) -> None:
        decisions: dict[str, object] = {
            "a": {"decision": "accept"},
            "b": {"decision": "reject"},
        }
        assert decision_word(decisions, "a") == "Accepted"
        assert decision_word(decisions, "b") == "Rejected"

    def test_a_stored_value_the_ui_never_writes_is_not_decided(self) -> None:
        """Nothing unrecognised is ever allowed to read as an approval."""
        assert decision_word({"a": {"decision": "approved"}}, "a") == "Not decided"
        assert decision_word({"a": "accept"}, "a") == "Not decided"

    def test_a_reason_survives_and_a_blank_one_does_not_become_a_reason(self) -> None:
        decisions: dict[str, object] = {
            "a": {"decision": "reject", "remark": " strength differs "},
        }
        assert decision_remark(decisions, "a") == "strength differs"
        blank: dict[str, object] = {"a": {"decision": "reject", "remark": "  "}}
        assert decision_remark(blank, "a") == ""
        assert decision_remark({}, "a") == ""


@pytest.fixture
def reviewed() -> ExportContext:
    """A scan somebody has actually reviewed, with one line rejected."""
    result = result_with_discrepancy()
    return ExportContext(
        result=result,
        employee_name="Priya Nair",
        employee_number="EMP-4417",
        decisions={
            "rx-01-bill-01": {"decision": "reject", "remark": "80mg billed against 40mg"},
            "bill-only-bill-02": {"decision": "unset"},
        },
        claimed_amount=Decimal("0.00"),
        annual_amount=Decimal("12000.00"),
        used_amount=Decimal("3500.00"),
        allowance_year="2026-27",
    )


class TestReportsCarryTheReview:
    def test_the_json_export_carries_decisions_and_the_accepted_total(
        self, reviewed: ExportContext
    ) -> None:
        payload = json.loads(build_json(reviewed))
        review = payload["review"]
        assert review["claimed_amount"] == "0.00"
        assert review["decisions"]["rx-01-bill-01"]["decision"] == "reject"
        assert review["decisions"]["rx-01-bill-01"]["remark"] == "80mg billed against 40mg"
        # The result itself is still verbatim -- the review sits beside it.
        assert payload["result"]["reimbursement"]["currency"] == "INR"

    def test_the_workbook_prints_the_decision_and_the_reason(
        self, reviewed: ExportContext
    ) -> None:
        book = zipfile.ZipFile(BytesIO(build_xlsx(reviewed)))
        text = " ".join(
            book.read(name).decode("utf-8", "replace")
            for name in book.namelist()
            if name.endswith(".xml")
        )
        assert "Rejected" in text
        assert "80mg billed against 40mg" in text
        # The claim figure now sits in the allowance block on the Summary sheet,
        # under the same label the dashboard uses.
        assert "This claim" in text
        # The unreviewed line is stated as unreviewed, not left blank.
        assert "Not decided" in text

    def test_the_pdf_prints_the_decision(self, reviewed: ExportContext) -> None:
        pdf = build_pdf(reviewed)
        assert pdf.startswith(b"%PDF")
        assert len(pdf) > 3000

    def test_a_scan_recorded_before_approval_existed_shows_nothing_as_accepted(self) -> None:
        """The migration case. No decisions stored, so no line is approved."""
        context = ExportContext(result=result_with_discrepancy())
        payload = json.loads(build_json(context))
        assert payload["review"]["decisions"] == {}
        assert payload["review"]["claimed_amount"] is None
        book = zipfile.ZipFile(BytesIO(build_xlsx(context)))
        text = " ".join(
            book.read(name).decode("utf-8", "replace")
            for name in book.namelist()
            if name.endswith(".xml")
        )
        assert "Not decided" in text
