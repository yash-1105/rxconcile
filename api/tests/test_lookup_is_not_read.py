"""A lookup failure is not a read failure. Third instance, fixed as a pattern.

Three separate bugs have now had the same shape: the system could not RECOGNISE
a name, and told the reader it could not READ the page. Becosules, then KFT,
then this — six billed lines on a perfectly legible lab bill, every one of them
remarked "some ordered tests could not be read".

Three states, and collapsing any two of them is the bug:

  * not read      -- no raw_text at all
  * read, unnamed -- raw_text present, test_name null
  * read, unknown -- a name lab_panels does not hold

The fixture text is verbatim from the real documents that exposed it.
"""

from __future__ import annotations

from rxconcile.export.rows import test_label as label_of
from rxconcile.models import BilledTest, PrescribedTest, Prescription, Submission
from rxconcile.normalize.lab_panels import resolve
from rxconcile.reconcile.lab import _identified

#: Exactly as the extractor returned them from the handwritten prescription.
#: test_name is null; raw_text holds the whole line.
REAL_ORDERS = [
    ("test-01", "Plasma Glucose < F / PP after a month"),
    ("test-02", "Plasma glucose PP after a month"),
]

#: Exactly as returned from the Dr Lal PathLabs bill LB-3390.
REAL_BILLED = [
    "HbA1c (Glycosylated Haemoglobin)",
    "Lipid Profile — Total Cholesterol",
    "Lipid Profile — HDL Cholesterol",
    "Lipid Profile — LDL Cholesterol",
    "Lipid Profile — Triglycerides",
    "Vitamin D (25-OH)",
]


def ordered(item_id: str, raw: str, name: str | None = None) -> PrescribedTest:
    return PrescribedTest(item_id=item_id, raw_text=raw, test_name=name, confidence=0.9)


def billed(item_id: str, raw: str, name: str | None = None) -> BilledTest:
    return BilledTest(item_id=item_id, raw_text=raw, test_name=name, confidence=0.9)


class TestIdentificationIsNotLegibility:
    """`_identified` decides whether a line can be RULED OUT as the missing test.

    It was called `_legible`, which was wrong twice over: it does not measure
    whether the page was read, and it is not the dictionary lookup either. The
    name is what taught three readers the wrong model.
    """

    def test_a_line_with_no_parsed_name_is_not_identified(self) -> None:
        """A smudge could be anything, including the test we cannot find, so it
        is not ruled out and an accusation about that test is softened."""
        assert not _identified(billed("billtest-99", "~~ smudge ~~", None))

    def test_a_line_with_a_name_our_tables_lack_is_identified(self) -> None:
        """"Vitamin D (25-OH)" is read perfectly and is simply not ours.

        A known, DIFFERENT test, so it cannot be the missing one and nothing is
        softened on its account.
        """
        assert _identified(billed("billtest-06", "Vitamin D (25-OH) Serum 1200.00",
                                  "Vitamin D (25-OH)"))

    def test_the_softened_finding_never_claims_the_page_was_unread(self) -> None:
        """The wording rule this whole file exists for.

        Nothing can tell a poor photograph from a string that would not parse,
        so the finding must not say "could not be read" -- that picks one cause,
        and picks the one that blames the submitter's photograph.

        Asserted on the MESSAGE a reader actually sees, not on the source.
        """
        from rxconcile.models import PharmacyBill
        from rxconcile.reconcile.lab import reconcile_tests

        rx = Prescription(
            overall_legibility=0.9, investigations_present=True,
            tests=[ordered("test-01", "CBC", "CBC")],
        )
        smudged = PharmacyBill(tests=[billed("billtest-01", "~~ smudge ~~", None)])
        out = reconcile_tests(rx, smudged, Submission(lab_bill_supplied=True,
                                                      lab_bill_tests_read=1))
        softened = [f for f in out.findings if f.rule_code == "TEST_NOT_BILLED"]
        assert softened, "the missing CBC must still be reported"
        message = softened[0].message
        # Still softened -- the smudge might BE the CBC.
        assert softened[0].severity == "warning"
        assert "could not be identified" in message
        assert "could not be read" not in message


class TestTheRealOrdersResolve:
    """The chain that produced both defects.

    Neither ordered test resolved, so every billed line was softened as though
    the prescription could not be read, and both ordered rows displayed nothing.
    """

    def test_both_handwritten_orders_resolve(self) -> None:
        for _, raw in REAL_ORDERS:
            assert resolve(raw).resolved, f"{raw!r} must resolve"

    def test_a_trailing_instruction_is_not_part_of_the_name(self) -> None:
        assert resolve("HbA1c after 3 months").name == "HbA1c"
        assert resolve("CBC stat").name == "Complete Blood Count"

    def test_one_half_of_a_pair_does_not_order_both(self) -> None:
        """An order for PP alone must not read as ordering fasting too, or the
        unbilled half becomes a TEST_NOT_BILLED nobody ordered."""
        assert resolve("Plasma glucose PP after a month").components == (
            "Glucose Post Prandial",
        )

    def test_every_billed_line_on_the_real_lab_bill_resolves(self) -> None:
        for raw in REAL_BILLED:
            assert resolve(raw).resolved, f"{raw!r} must resolve"


class TestARowAlwaysSaysWhatItIsAbout:
    def test_a_null_name_falls_back_to_the_line_on_the_page(self) -> None:
        """A row that is an em-dash in every column is a row about nothing.

        raw_text is never nulled, so there is always something honest to show.
        """
        for item_id, raw in REAL_ORDERS:
            assert label_of(ordered(item_id, raw)) == raw

    def test_a_parsed_name_is_preferred(self) -> None:
        assert label_of(billed("b1", "HbA1c ... 550.00", "HbA1c")) == "HbA1c"

    def test_nothing_at_all_stays_nothing(self) -> None:
        """Only a genuinely empty line has no label, and it must not invent one."""
        assert label_of(billed("b2", "  ", None)) is None
        assert label_of(None) is None
