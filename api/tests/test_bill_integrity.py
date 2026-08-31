"""Bill-integrity checks: arithmetic, GSTIN, drug licence, non-medicine lines.

The tests that carry weight here are the false-positive guards. A billing
accusation against a pharmacy that turns out to be a rounding difference or an
unprinted discount costs more credibility than the check ever earns.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from rxconcile.models import BilledItem, Finding, PharmacyBill, PrescribedItem, Prescription
from rxconcile.models.schema import CHECK_UNAVAILABLE_CODE
from rxconcile.reconcile import engine
from rxconcile.reconcile.arithmetic import check_arithmetic
from rxconcile.validate import check_gstin, check_licence, gstin_check_digit

VALID_GSTIN = "27AAPFU0939F1ZV"


def item(
    item_id: str = "bill-01",
    *,
    name: str = "Dolo",
    quantity: float | None = 10.0,
    unit_price: str | None = "2.20",
    discount: str | None = None,
    line_total: str | None = "22.00",
    form: str | None = "tablet",
) -> BilledItem:
    return BilledItem(
        item_id=item_id, raw_text=name, drug_name=name, form=form, quantity=quantity,
        unit_price=Decimal(unit_price) if unit_price else None,
        discount=Decimal(discount) if discount else None,
        line_total=Decimal(line_total) if line_total else None,
        confidence=0.9,
    )


def bill(*items: BilledItem, **kwargs: object) -> PharmacyBill:
    base: dict[str, object] = {
        "items": list(items), "currency": "INR",
        "pharmacy_licence_no": "TN/2019/337821", "gstin": VALID_GSTIN,
    }
    base.update(kwargs)
    return PharmacyBill.model_validate(base)


def codes(findings: list[Finding], code: str) -> list[str]:
    return [f.severity for f in findings if f.rule_code == code]


# ---------------------------------------------------------------------------
# Line arithmetic
# ---------------------------------------------------------------------------


def test_a_line_that_adds_up_raises_nothing() -> None:
    assert not codes(check_arithmetic(bill(item())), "LINE_TOTAL_MISMATCH")


def test_a_line_that_does_not_add_up_is_a_warning() -> None:
    found = check_arithmetic(bill(item(line_total="45.00")))
    assert codes(found, "LINE_TOTAL_MISMATCH") == ["warning"]
    detail = next(f.detail for f in found if f.rule_code == "LINE_TOTAL_MISMATCH")
    assert detail["expected"] == "22.00"
    assert detail["charged"] == "45.00"


@pytest.mark.parametrize("charged", ["22.00", "22.04", "21.96"])
def test_rounding_within_tolerance_is_not_a_mismatch(charged: str) -> None:
    """Indian bills round to the rupee constantly."""
    assert not codes(check_arithmetic(bill(item(line_total=charged))), "LINE_TOTAL_MISMATCH")


def test_a_printed_discount_is_subtracted_before_comparing() -> None:
    found = check_arithmetic(bill(item(discount="2.00", line_total="20.00")))
    assert not codes(found, "LINE_TOTAL_MISMATCH")


def test_a_printed_discount_that_still_does_not_reconcile_is_reported() -> None:
    found = check_arithmetic(bill(item(discount="2.00", line_total="15.00")))
    assert codes(found, "LINE_TOTAL_MISMATCH") == ["warning"]


def test_an_unprinted_discount_shaped_shortfall_is_not_an_accusation() -> None:
    """A cheaper line with no discount column is a discount, not an error.

    The two are indistinguishable from the page, and only one is worth
    accusing a pharmacy of.
    """
    assert not codes(check_arithmetic(bill(item(line_total="19.80"))), "LINE_TOTAL_MISMATCH")


def test_a_shortfall_too_deep_to_be_a_discount_is_still_reported() -> None:
    # 22.00 charged as 5.00 is a 77% shortfall, well past any plausible discount.
    assert codes(check_arithmetic(bill(item(line_total="5.00"))), "LINE_TOTAL_MISMATCH") == [
        "warning"
    ]


def test_being_overcharged_is_always_reported() -> None:
    """A discount explains a cheaper line. Nothing explains a dearer one."""
    assert codes(check_arithmetic(bill(item(line_total="30.00"))), "LINE_TOTAL_MISMATCH") == [
        "warning"
    ]


def test_a_line_missing_a_price_cannot_fail_an_arithmetic_check() -> None:
    found = check_arithmetic(bill(item(unit_price=None)))
    assert not codes(found, "LINE_TOTAL_MISMATCH")
    unavailable = [
        f for f in found
        if f.rule_code == CHECK_UNAVAILABLE_CODE and f.detail.get("check") == "line arithmetic"
    ]
    assert len(unavailable) == 1, "it must say the check could not run"


# ---------------------------------------------------------------------------
# Subtotal and grand total
# ---------------------------------------------------------------------------


def test_a_subtotal_matching_its_lines_raises_nothing() -> None:
    found = check_arithmetic(bill(item("bill-01"), item("bill-02"), subtotal=Decimal("44.00")))
    assert not codes(found, "SUBTOTAL_MISMATCH")


def test_a_subtotal_that_does_not_match_is_a_warning() -> None:
    found = check_arithmetic(bill(item("bill-01"), item("bill-02"), subtotal=Decimal("99.00")))
    assert codes(found, "SUBTOTAL_MISMATCH") == ["warning"]


def test_a_subtotal_within_one_rupee_is_accepted() -> None:
    found = check_arithmetic(bill(item("bill-01"), item("bill-02"), subtotal=Decimal("44.90")))
    assert not codes(found, "SUBTOTAL_MISMATCH")


def test_a_bill_level_discount_explains_a_lower_subtotal() -> None:
    found = check_arithmetic(
        bill(item("bill-01"), item("bill-02"),
             subtotal=Decimal("40.00"), discount_total=Decimal("4.00"))
    )
    assert not codes(found, "SUBTOTAL_MISMATCH")


def test_grand_total_is_checked_against_subtotal_plus_tax() -> None:
    ok = check_arithmetic(bill(item(), subtotal=Decimal("22.00"),
                               tax_total=Decimal("2.64"), grand_total=Decimal("24.64")))
    assert not codes(ok, "GRAND_TOTAL_MISMATCH")
    bad = check_arithmetic(bill(item(), subtotal=Decimal("22.00"),
                                tax_total=Decimal("2.64"), grand_total=Decimal("99.00")))
    assert codes(bad, "GRAND_TOTAL_MISMATCH") == ["warning"]


def test_tax_inclusive_pricing_is_detected_and_skipped() -> None:
    """Grand total equal to subtotal means the rates already include tax."""
    found = check_arithmetic(bill(item(), subtotal=Decimal("22.00"),
                                  tax_total=Decimal("2.64"), grand_total=Decimal("22.00")))
    assert not codes(found, "GRAND_TOTAL_MISMATCH")
    assert codes(found, "TAX_INCLUSIVE_PRICING") == ["info"]


def test_absent_totals_report_that_the_check_could_not_run() -> None:
    found = check_arithmetic(bill(item()))
    checks = {
        f.detail.get("check") for f in found if f.rule_code == CHECK_UNAVAILABLE_CODE
    }
    assert "subtotal" in checks
    assert "grand total" in checks
    assert not codes(found, "SUBTOTAL_MISMATCH")
    assert not codes(found, "GRAND_TOTAL_MISMATCH")


# ---------------------------------------------------------------------------
# GSTIN
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "gstin",
    ["27AAPFU0939F1ZV", "29AAGCB7383J1Z4", "07AAGFF2194N1Z1", "24AAACC1206D1ZM",
     "09AAACH7409R1ZZ"],
)
def test_published_gstins_pass_the_checksum(gstin: str) -> None:
    check = check_gstin(gstin)
    assert check.well_formed, check.reason


@pytest.mark.parametrize(
    ("corrupted", "fragment"),
    [
        ("27AAPFU0939F1ZW", "check digit"),          # last character changed
        ("27AAPFU0939F1ZX", "check digit"),
        ("27AAPFU9039F1ZV", "check digit"),          # two digits transposed
        ("99AAPFU0939F1ZV", "state code"),           # state code out of range
        ("27AAPFU0939F1YV", "pattern"),              # the literal Z changed
        ("27AAPFU0939F1Z", "characters"),            # too short
        ("27AAPFU0939F1ZVX", "characters"),          # too long
    ],
)
def test_a_corrupted_gstin_is_caught(corrupted: str, fragment: str) -> None:
    check = check_gstin(corrupted)
    assert not check.well_formed
    assert check.reason is not None
    assert fragment in check.reason


def test_transposition_is_caught_not_just_substitution() -> None:
    """The quotient-plus-remainder step is what makes this sensitive."""
    assert gstin_check_digit("27AAPFU0939F1Z") != gstin_check_digit("27AAPFU9039F1Z")


def test_an_absent_gstin_is_absent_not_invalid() -> None:
    for raw in (None, "", "   "):
        check = check_gstin(raw)
        assert not check.present
        assert not check.well_formed
        assert check.reason is None, "nothing was printed, so nothing failed"


def test_gstin_copy_never_claims_a_registry_was_consulted() -> None:
    rx = Prescription(overall_legibility=0.9)
    result = engine.reconcile(
        rx, bill(item(), gstin="27AAPFU0939F1ZW"), processing_ms=0
    )
    found = next(f for f in result.findings if f.rule_code == "GSTIN_INVALID")
    lowered = found.message.lower()
    assert "not a valid gstin format" in lowered
    assert "no registry was consulted" in lowered
    for forbidden in ("not registered", "does not exist", "unregistered", "verified"):
        assert forbidden not in lowered
    assert found.detail["scope"] == "format_and_checksum_only"


def test_a_state_mismatch_is_only_ever_information() -> None:
    rx = Prescription(overall_legibility=0.9)
    result = engine.reconcile(
        rx,
        bill(item(), gstin=VALID_GSTIN, pharmacy_address="12 Anna Salai, Chennai, Tamil Nadu"),
        processing_ms=0,
    )
    mismatch = [f for f in result.findings if f.rule_code == "GSTIN_STATE_MISMATCH"]
    assert [f.severity for f in mismatch] == ["info"], "a chain may bill from another state"


def test_a_matching_state_raises_nothing() -> None:
    rx = Prescription(overall_legibility=0.9)
    result = engine.reconcile(
        rx, bill(item(), gstin=VALID_GSTIN, pharmacy_address="Pune, Maharashtra"),
        processing_ms=0,
    )
    assert not [f for f in result.findings if f.rule_code == "GSTIN_STATE_MISMATCH"]


# ---------------------------------------------------------------------------
# Drug licence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "licence",
    ["TN/2019/337821", "KA-B-21/1234", "20B/MH/1998/554", "DL-20B-441", "MH-MUM-123456",
     "anything at all"],
)
def test_no_licence_format_is_ever_rejected(licence: str) -> None:
    """36 authorities, 36 conventions. Rejecting a valid licence is worse than
    not checking one."""
    assert check_licence(licence).present
    rx = Prescription(overall_legibility=0.9)
    result = engine.reconcile(rx, bill(item(), pharmacy_licence_no=licence), processing_ms=0)
    assert not [f for f in result.findings if f.rule_code == "LICENCE_ABSENT"]


def test_an_absent_licence_is_a_warning() -> None:
    rx = Prescription(overall_legibility=0.9)
    result = engine.reconcile(rx, bill(item(), pharmacy_licence_no=None), processing_ms=0)
    found = [f for f in result.findings if f.rule_code == "LICENCE_ABSENT"]
    assert [f.severity for f in found] == ["warning"]


def test_the_licence_check_never_offers_a_validity_verdict() -> None:
    check = check_licence("TN/2019/337821")
    assert not hasattr(check, "valid")
    assert "no format validation was attempted" in check.note


# ---------------------------------------------------------------------------
# Non-medicine lines
# ---------------------------------------------------------------------------


def non_medicine_codes(result: object) -> list[str]:
    return [
        f.severity for f in result.findings  # type: ignore[attr-defined]
        if f.rule_code == "NON_MEDICINE_ITEM"
    ]


@pytest.mark.parametrize(
    "name",
    ["Delivery Charges", "Dettol Soap", "Accu-Chek Test Strips", "Whey Protein Powder",
     "Surgical Gloves", "Sanitary Napkin"],
)
def test_a_non_medicine_line_is_flagged_as_info(name: str) -> None:
    rx = Prescription(overall_legibility=0.9)
    result = engine.reconcile(
        rx, bill(item(name=name, form=None)), processing_ms=0
    )
    assert non_medicine_codes(result) == ["info"]


def test_a_real_medicine_is_never_reclassified() -> None:
    """The worst outcome available here: a medicine silently dropped."""
    rx = Prescription(
        overall_legibility=0.9,
        items=[PrescribedItem(item_id="rx-01", raw_text="Zincovit", drug_name="Zincovit",
                              confidence=0.9)],
    )
    result = engine.reconcile(rx, bill(item(name="Zincovit")), processing_ms=0)
    assert non_medicine_codes(result) == []


def test_an_unrecognised_line_is_left_unclassified_not_guessed() -> None:
    rx = Prescription(overall_legibility=0.9)
    result = engine.reconcile(rx, bill(item(name="Zzq Unknown Thing", form=None)),
                              processing_ms=0)
    assert non_medicine_codes(result) == [], "unclassified is a valid state"


def test_a_non_medicine_finding_never_suppresses_another() -> None:
    """It is info, and it must not soften what else is against the line."""
    rx = Prescription(overall_legibility=0.9)
    result = engine.reconcile(rx, bill(item(name="Dettol Soap", form=None)), processing_ms=0)
    assert non_medicine_codes(result) == ["info"]
    assert [f.severity for f in result.findings if f.rule_code == "BILL_NOT_PRESCRIBED"]


def test_a_non_medicine_line_is_its_own_reimbursement_category() -> None:
    """Never folded into 'not on prescription' -- that reads as an accusation."""
    rx = Prescription(overall_legibility=0.9)
    result = engine.reconcile(
        rx, bill(item(name="Delivery Charges", form=None, line_total="40.00",
                      quantity=1.0, unit_price="40.00")),
        processing_ms=0,
    )
    money = result.reimbursement
    assert money.non_medicine_line_count == 1
    assert money.non_medicine_total == Decimal("40.00")
    assert money.not_eligible_total == Decimal("0")
    line = next(x for x in money.lines if x.category == "non_medicine")
    assert "outside reimbursement" in line.reason


def test_the_subtotal_includes_lab_lines_not_just_medicines() -> None:
    """A bill's subtotal covers both sections.

    Summing only the medicines reported a shortfall on a bill whose lab
    section was simply not counted — a false accusation manufactured by
    looking at half the document.
    """
    from rxconcile.models import BilledTest

    bl = bill(
        item(line_total="22.00"),
        tests=[
            BilledTest(item_id="billtest-01", raw_text="CBC", test_name="CBC",
                       quantity=1.0, unit_price=Decimal("450.00"),
                       line_total=Decimal("450.00"), confidence=0.9)
        ],
        subtotal=Decimal("472.00"),
    )
    assert not codes(check_arithmetic(bl), "SUBTOTAL_MISMATCH")


def test_a_lab_line_that_does_not_add_up_is_still_caught() -> None:
    from rxconcile.models import BilledTest

    bl = bill(
        item(),
        tests=[
            BilledTest(item_id="billtest-01", raw_text="CBC", test_name="CBC",
                       quantity=2.0, unit_price=Decimal("450.00"),
                       line_total=Decimal("50.00"), confidence=0.9)
        ],
    )
    assert codes(check_arithmetic(bl), "LINE_TOTAL_MISMATCH") == ["warning"]


# ---------------------------------------------------------------------------
# Unresolved in the dictionary is not the same as unreadable
# ---------------------------------------------------------------------------


def test_a_legible_non_medicine_is_never_reported_as_unreadable() -> None:
    """LAKME SUNSCREEN was read perfectly. The dictionary simply has no cosmetics.

    `identified` is the drug-dictionary lookup and `legible` is whether the page
    was read; conflating them blamed the extraction for a gap in our reference
    data.
    """
    rx = Prescription(overall_legibility=0.9)
    result = engine.reconcile(
        rx, bill(item(name="LAKME SUNSCREEN SPF50", form="other")), processing_ms=0
    )
    found = next(f for f in result.findings if f.rule_code == "BILL_NOT_PRESCRIBED")
    assert found.detail["legible"] is True, "the line was read"
    assert found.detail["identified"] is False, "and is not in the drug dictionary"


def test_a_genuinely_unreadable_line_still_says_so() -> None:
    bl = PharmacyBill(
        currency="INR", pharmacy_licence_no="TN/2019/337821", gstin=VALID_GSTIN,
        items=[BilledItem(item_id="bill-01", raw_text="[?] smudged", drug_name=None,
                          confidence=0.3)],
    )
    result = engine.reconcile(Prescription(overall_legibility=0.9), bl, processing_ms=0)
    found = next(f for f in result.findings if f.rule_code == "BILL_NOT_PRESCRIBED")
    assert found.detail["legible"] is False


@pytest.mark.parametrize("name", ["LAKME SUNSCREEN SPF50", "DELIVERY CHARGE"])
def test_the_two_lines_from_the_real_bill_are_classified(name: str) -> None:
    rx = Prescription(overall_legibility=0.9)
    result = engine.reconcile(rx, bill(item(name=name, form="other")), processing_ms=0)
    assert non_medicine_codes(result) == ["info"]


# ---------------------------------------------------------------------------
# Panel-covered lines are accounted for, not left for a human
# ---------------------------------------------------------------------------


def test_every_line_a_panel_covers_counts_as_covered() -> None:
    """An ordered panel billed as four analytes pairs to one of them.

    Reading only the pair left the other three in "needs a manual check" with
    no reason attached to them at all, and understated what the prescription
    covers.
    """
    from rxconcile.models import BilledTest, PrescribedTest

    rx = Prescription(
        overall_legibility=0.9, investigations_present=True,
        tests=[PrescribedTest(item_id="test-01", raw_text="Adv: Lipid Profile",
                              test_name="Lipid Profile", confidence=0.9)],
    )
    names = [
        ("Lipid Profile — Total Cholesterol", "180.00"),
        ("Lipid Profile — HDL", "90.00"),
        ("Lipid Profile — LDL", "90.00"),
        ("Lipid Profile — Triglycerides", "90.00"),
    ]
    bl = PharmacyBill(
        currency="INR", pharmacy_licence_no="TN/2019/337821", gstin=VALID_GSTIN,
        tests=[
            BilledTest(item_id=f"billtest-{i:02d}", raw_text=n, test_name=n,
                       quantity=1.0, line_total=Decimal(t), confidence=0.9)
            for i, (n, t) in enumerate(names, start=1)
        ],
    )
    money = engine.reconcile(rx, bl, processing_ms=0).reimbursement
    assert money.eligible_line_count == 4, "all four analytes are covered"
    assert money.eligible_total == Decimal("450.00")
    assert money.needs_review_line_count == 0


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------

import datetime as _dt  # noqa: E402

from rxconcile.extract._runner import resolve_expiry  # noqa: E402

BILL_DAY = _dt.date(2026, 8, 6)


def dated_bill(*items: BilledItem, **kwargs: object) -> PharmacyBill:
    return bill(*items, bill_date=BILL_DAY, **kwargs)


def expiring(expiry: _dt.date | None, item_id: str = "bill-01") -> BilledItem:
    return BilledItem(
        item_id=item_id, raw_text="ASPIRIN 75 TAB", drug_name="Aspirin", form="tablet",
        quantity=30.0, unit_price=Decimal("1.10"), line_total=Decimal("33.00"),
        expiry=expiry, confidence=0.9,
    )


@pytest.mark.parametrize(
    ("printed", "resolved"),
    [
        ("07/2026", _dt.date(2026, 7, 31)),
        ("03/2027", _dt.date(2027, 3, 31)),
        ("07/26", _dt.date(2026, 7, 31)),
        ("JUL 2026", _dt.date(2026, 7, 31)),
        ("Jul-26", _dt.date(2026, 7, 31)),
        ("2026-07", _dt.date(2026, 7, 31)),
        ("SEPT 2027", _dt.date(2027, 9, 30)),
        ("02/2028", _dt.date(2028, 2, 29)),
    ],
)
def test_an_expiry_resolves_to_the_last_valid_day(printed: str, resolved: _dt.date) -> None:
    """'07/2026' means good THROUGH 31 July, not until the 1st."""
    assert resolve_expiry(printed)[0] == resolved


@pytest.mark.parametrize("printed", ["13/2026", "00/2026", "garbage", "-", "", None])
def test_an_unrecognised_expiry_is_refused_not_guessed(printed: str | None) -> None:
    assert resolve_expiry(printed)[0] is None


def test_an_ambiguous_full_date_expiry_stays_unresolved() -> None:
    """Handed to the existing resolver, so its ambiguity rules still apply."""
    assert resolve_expiry("06-08-2026")[0] is None


def test_dispensing_after_expiry_is_critical() -> None:
    result = engine.reconcile(
        Prescription(overall_legibility=0.9),
        dated_bill(expiring(_dt.date(2026, 7, 31))),
        processing_ms=0,
    )
    assert codes(result.findings, "EXPIRED_ITEM") == ["critical"]
    detail = next(f.detail for f in result.findings if f.rule_code == "EXPIRED_ITEM")
    assert detail["days_past_expiry"] == 6


def test_dispensing_on_the_last_valid_day_is_not_expired() -> None:
    """A bill dated the 31st of an 07/2026 line is still inside its shelf life."""
    result = engine.reconcile(
        Prescription(overall_legibility=0.9),
        bill(expiring(_dt.date(2026, 7, 31)), bill_date=_dt.date(2026, 7, 31)),
        processing_ms=0,
    )
    assert not codes(result.findings, "EXPIRED_ITEM")


def test_expiring_within_thirty_days_is_a_warning() -> None:
    result = engine.reconcile(
        Prescription(overall_legibility=0.9),
        dated_bill(expiring(_dt.date(2026, 8, 31))),
        processing_ms=0,
    )
    assert codes(result.findings, "EXPIRY_NEAR") == ["warning"]
    assert not codes(result.findings, "EXPIRED_ITEM")


def test_a_comfortable_shelf_life_raises_nothing() -> None:
    result = engine.reconcile(
        Prescription(overall_legibility=0.9),
        dated_bill(expiring(_dt.date(2027, 3, 31))),
        processing_ms=0,
    )
    assert not codes(result.findings, "EXPIRED_ITEM")
    assert not codes(result.findings, "EXPIRY_NEAR")


def test_no_bill_date_means_no_line_can_be_cleared_of_expiry() -> None:
    """An undated bill must not clear a medicine of being expired."""
    result = engine.reconcile(
        Prescription(overall_legibility=0.9),
        bill(expiring(_dt.date(2020, 1, 31))),
        processing_ms=0,
    )
    assert not codes(result.findings, "EXPIRED_ITEM")
    checks = [
        f for f in result.findings
        if f.rule_code == CHECK_UNAVAILABLE_CODE and f.detail.get("check") == "expiry"
    ]
    assert checks, "it must say the expiry check could not run"


def test_a_line_with_no_expiry_is_reported_not_cleared() -> None:
    result = engine.reconcile(
        Prescription(overall_legibility=0.9), dated_bill(expiring(None)), processing_ms=0
    )
    assert not codes(result.findings, "EXPIRED_ITEM")
    assert [
        f for f in result.findings
        if f.rule_code == CHECK_UNAVAILABLE_CODE and f.detail.get("check") == "expiry"
    ]


# ---------------------------------------------------------------------------
# The subtotal regression
# ---------------------------------------------------------------------------


def test_a_subtotal_below_the_lines_is_reported_not_swallowed() -> None:
    """A 190-rupee gap passed unreported as an assumed unitemised discount.

    That silently accepted any subtotal error up to 30% of the bill.
    """
    found = check_arithmetic(
        bill(item("bill-01", line_total="1258.00", quantity=1.0, unit_price="1258.00"),
             item("bill-02", line_total="900.00", quantity=1.0, unit_price="900.00"),
             subtotal=Decimal("1968.00"))
    )
    assert codes(found, "SUBTOTAL_MISMATCH") == ["warning"]
    detail = next(f.detail for f in found if f.rule_code == "SUBTOTAL_MISMATCH")
    assert detail["difference"] == "-190.00"
    assert detail["possible_unitemised_discount"] is True


def test_the_wording_offers_the_discount_explanation_without_assuming_it() -> None:
    found = check_arithmetic(
        bill(item(line_total="100.00", quantity=1.0, unit_price="100.00"),
             subtotal=Decimal("90.00"))
    )
    message = next(f.message for f in found if f.rule_code == "SUBTOTAL_MISMATCH")
    assert "No discount is printed" in message


def test_a_printed_bill_level_discount_still_explains_the_gap() -> None:
    found = check_arithmetic(
        bill(item(line_total="100.00", quantity=1.0, unit_price="100.00"),
             subtotal=Decimal("90.00"), discount_total=Decimal("10.00"))
    )
    assert not codes(found, "SUBTOTAL_MISMATCH")


def test_the_subtotal_sum_includes_non_medicine_lines() -> None:
    """A display filter must never change an arithmetic check.

    Excluding a sunscreen and a delivery charge from the sum would produce a
    different figure and could manufacture a spurious match.
    """
    found = check_arithmetic(
        bill(
            item("bill-01", name="Dolo", line_total="100.00", quantity=1.0,
                 unit_price="100.00"),
            item("bill-02", name="LAKME SUNSCREEN SPF50", form="other",
                 line_total="399.00", quantity=1.0, unit_price="399.00"),
            item("bill-03", name="DELIVERY CHARGE", form="other", line_total="40.00",
                 quantity=1.0, unit_price="40.00"),
            subtotal=Decimal("539.00"),
        )
    )
    assert not codes(found, "SUBTOTAL_MISMATCH"), "all three lines must be summed"
    # And dropping them would have been noticed:
    wrong = check_arithmetic(
        bill(item("bill-01", name="Dolo", line_total="100.00", quantity=1.0,
                  unit_price="100.00"), subtotal=Decimal("539.00"))
    )
    assert codes(wrong, "SUBTOTAL_MISMATCH") == ["warning"]
