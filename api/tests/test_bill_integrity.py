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
