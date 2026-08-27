"""Normalisation layer: dictionary, matcher, units, sig."""

from __future__ import annotations

import pytest

from rxconcile.normalize import drug_dictionary as dd
from rxconcile.normalize import sig, units
from rxconcile.normalize.matcher import MIN_SCORE, clean_drug_string, resolve

# ==========================================================================
# drug_dictionary
# ==========================================================================


def test_dictionary_loads_a_substantial_list() -> None:
    entries = dd.load_entries()
    assert len(entries) >= 250


def test_brand_names_are_unique() -> None:
    keys = [entry.key for entry in dd.load_entries()]
    assert len(keys) == len(set(keys))


def test_every_entry_has_a_salt() -> None:
    assert all(entry.salt_composition for entry in dd.load_entries())


@pytest.mark.parametrize(
    ("brand", "salt"),
    [
        ("Dolo", "Paracetamol"),
        ("Calpol", "Paracetamol"),
        ("Crocin", "Paracetamol"),
        ("Augmentin", "Amoxicillin+Clavulanic Acid"),
        ("Pan", "Pantoprazole"),
        ("Pantocid", "Pantoprazole"),
        ("Azithral", "Azithromycin"),
        ("Telma", "Telmisartan"),
        ("Ecosprin", "Aspirin"),
        ("Montair-LC", "Montelukast+Levocetirizine"),
        ("Zerodol", "Aceclofenac"),
        ("Glycomet", "Metformin"),
    ],
)
def test_high_frequency_brands_map_to_expected_salts(brand: str, salt: str) -> None:
    entry = dd.find_brand(brand)
    assert entry is not None, brand
    assert entry.salt_composition == salt


def test_pan_and_pan_d_are_different_drugs() -> None:
    """The single most dangerous confusion in this dictionary."""
    plain = dd.find_brand("Pan")
    combo = dd.find_brand("Pan-D")
    assert plain is not None and combo is not None
    assert plain.salt_composition != combo.salt_composition
    assert not plain.is_combination
    assert combo.is_combination


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Pan-D", "PAN D"), ("PAN D", "PAN D"), ("pan  d.", "PAN D"), ("Zerodol-SP", "ZERODOL SP")],
)
def test_normalize_name_folds_punctuation(raw: str, expected: str) -> None:
    assert dd.normalize_name(raw) == expected


def test_schedule_h_and_h1_are_marked() -> None:
    h1 = list(dd.scheduled_entries("H1"))
    assert len(h1) >= 10
    assert all(entry.is_schedule_h1 for entry in h1)
    assert all(entry.requires_prescription for entry in h1)


def test_otc_entries_do_not_require_prescription() -> None:
    otc = [entry for entry in dd.load_entries() if entry.schedule == "OTC"]
    assert otc
    assert not any(entry.requires_prescription for entry in otc)


def test_combination_salts_split_on_plus() -> None:
    entry = dd.find_brand("Augmentin")
    assert entry is not None
    assert entry.salts == ("Amoxicillin", "Clavulanic Acid")


def test_entries_for_salt_finds_every_brand() -> None:
    brands = {e.brand_name for e in dd.entries_for_salt("Paracetamol")}
    assert {"Dolo", "Calpol", "Crocin"} <= brands


# ==========================================================================
# matcher
# ==========================================================================


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("TAB. DOLO 650", "DOLO"),
        ("Dolo-650", "DOLO"),
        ("Cap Augmentin 625 BD x 5/7", "AUGMENTIN"),
        ("T. Pan 40 OD × 10d", "PAN"),
        ("Syp Ascoril 100ml", "ASCORIL"),
        ("INJ MONOCEF 1G", "MONOCEF"),
        ("Tab Mydocalm 50mg", "MYDOCALM"),
    ],
)
def test_clean_strips_form_strength_and_sig(raw: str, cleaned: str) -> None:
    assert clean_drug_string(raw) == cleaned


@pytest.mark.parametrize(
    ("raw", "name", "method"),
    [
        ("TAB. DOLO 650", "Dolo", "exact"),
        ("Dolo-650", "Dolo", "exact"),
        ("Cap Augmentin 625 BD x 5/7", "Augmentin", "exact"),
        ("T. Pan 40 OD × 10d", "Pan", "exact"),
        ("Pan-D", "Pan-D", "exact"),
        ("Montair-LC", "Montair-LC", "exact"),
        ("Zerodol-SP", "Zerodol-SP", "exact"),
        ("Ecosprin 75", "Ecosprin", "exact"),
        ("Glycomet 500", "Glycomet", "exact"),
        ("Telma 40", "Telma", "exact"),
    ],
)
def test_exact_brand_resolution(raw: str, name: str, method: str) -> None:
    result = resolve(raw)
    assert result.name == name
    assert result.method == method
    assert result.match_score == 100.0


def test_salt_level_resolution() -> None:
    result = resolve("PARACETAMOL 650MG")
    assert result.method == "salt_equivalent"
    assert result.salt == "Paracetamol"


def test_salt_resolution_for_a_multi_brand_ingredient() -> None:
    assert resolve("Pantoprazole 40mg").method == "salt_equivalent"


def test_fuzzy_resolution_recovers_a_typo() -> None:
    result = resolve("AUGMENTN 625")
    assert result.name == "Augmentin"
    assert result.method == "fuzzy"
    assert result.match_score >= MIN_SCORE


def test_nonsense_is_unresolved_not_forced() -> None:
    result = resolve("banana bread")
    assert result.method == "unresolved"
    assert result.name is None
    assert result.salt is None
    assert result.match_score == 0.0


def test_none_and_blank_are_unresolved() -> None:
    assert resolve(None).method == "unresolved"
    assert resolve("").method == "unresolved"
    assert resolve("TAB.").method == "unresolved"


def test_never_resolves_below_threshold() -> None:
    """Every resolved match must clear MIN_SCORE."""
    for raw in ["Dolo", "AUGMENTN", "PARACETAMOL", "qwertyuiop", "zzzz 123"]:
        result = resolve(raw)
        if result.resolved:
            assert result.match_score >= MIN_SCORE, raw


def test_subset_match_does_not_silently_pick_the_shorter_brand() -> None:
    """'PAN DX' must not resolve to Pan (pantoprazole alone).

    token_set_ratio scores PAN against PAN DX at 100. Resolving there would
    substitute pantoprazole for pantoprazole+domperidone.
    """
    result = resolve("PAN DX")
    assert result.name != "Pan"
    assert result.salt != "Pantoprazole"


def test_resolved_flag_matches_method() -> None:
    assert resolve("Dolo").resolved is True
    assert resolve("banana bread").resolved is False


# ==========================================================================
# units — strengths
# ==========================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mg", "mg"), ("MG", "mg"), ("mgs", "mg"), ("Mg.", "mg"),
        ("g", "g"), ("gm", "g"), ("GM", "g"), ("gms", "g"),
        ("mcg", "mcg"), ("MCG", "mcg"), ("ug", "mcg"), ("µg", "mcg"),
        ("ml", "ml"), ("mL", "ml"), ("ML", "ml"),
        ("IU", "IU"), ("iu", "IU"), ("%", "%"),
    ],
)
def test_canonical_unit_variants(raw: str, expected: str) -> None:
    assert units.canonical_unit(raw) == expected


def test_unknown_unit_is_none_not_guessed() -> None:
    assert units.canonical_unit("banana") is None
    assert units.canonical_unit("") is None
    assert units.canonical_unit(None) is None


def test_grams_convert_to_milligrams() -> None:
    strength = units.normalize_strength(1, "g")
    assert strength is not None
    assert strength.value == 1000.0
    assert strength.unit == "mg"


def test_gram_and_milligram_compare_equal() -> None:
    assert units.strengths_equal(
        units.normalize_strength(1, "gm"), units.normalize_strength(1000, "mg")
    )


def test_unknown_unit_keeps_the_value() -> None:
    strength = units.normalize_strength(650, "banana")
    assert strength is not None
    assert strength.value == 650.0
    assert strength.unit is None


def test_missing_value_yields_none() -> None:
    assert units.normalize_strength(None, "mg") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("500mg", [(500.0, "mg")]),
        ("650 MG", [(650.0, "mg")]),
        ("500+125mg", [(500.0, "mg"), (125.0, "mg")]),
        ("37.5mg+325mg", [(37.5, "mg"), (325.0, "mg")]),
        ("1g", [(1000.0, "mg")]),
        ("60000IU", [(60000.0, "IU")]),
    ],
)
def test_parse_strength_components(raw: str, expected: list[tuple[float, str]]) -> None:
    parsed = units.parse_strength(raw)
    assert [(s.value, s.unit) for s in parsed] == expected


def test_parse_strength_of_nothing() -> None:
    assert units.parse_strength(None) == ()
    assert units.parse_strength("") == ()
    assert units.parse_strength("no digits here") == ()


# ==========================================================================
# units — pack sizes
# ==========================================================================


@pytest.mark.parametrize(
    ("raw", "expected_units"),
    [
        ("10'S", 10), ("10S", 10), ("10 TAB", 10), ("1x10", 10),
        ("STRIP OF 10", 10), ("1'S", 1), ("1 VIAL", 1), ("1 TUBE", 1),
        ("15'S", 15), ("2x15", 30), ("30 TABLETS", 30), ("PACK OF 4", 4),
        ("1 BOTTLE", 1),
    ],
)
def test_pack_size_unit_counts(raw: str, expected_units: int) -> None:
    parsed = units.parse_pack_size(raw)
    assert parsed is not None
    assert parsed.units_per_pack == expected_units
    assert parsed.volume_ml is None
    assert parsed.resolved


@pytest.mark.parametrize(
    ("raw", "expected_ml"),
    [("15ML", 15.0), ("100 ML", 100.0), ("60ml syrup", 60.0), ("1 L", 1000.0)],
)
def test_pack_size_volumes(raw: str, expected_ml: float) -> None:
    parsed = units.parse_pack_size(raw)
    assert parsed is not None
    assert parsed.volume_ml == expected_ml
    assert parsed.units_per_pack is None


def test_pack_size_volume_is_not_read_as_a_count() -> None:
    """'15ML' is a volume, not fifteen tablets."""
    parsed = units.parse_pack_size("15ML")
    assert parsed is not None
    assert parsed.units_per_pack is None


def test_unrecognised_pack_returns_unresolved_not_a_guess() -> None:
    parsed = units.parse_pack_size("BANANA PACK")
    assert parsed is not None
    assert parsed.units_per_pack is None
    assert parsed.volume_ml is None
    assert parsed.method == "unrecognised"
    assert not parsed.resolved


def test_blank_pack_is_none() -> None:
    assert units.parse_pack_size(None) is None
    assert units.parse_pack_size("   ") is None


def test_pack_keeps_the_raw_text() -> None:
    parsed = units.parse_pack_size("10'S")
    assert parsed is not None
    assert parsed.raw == "10'S"


# ==========================================================================
# sig — frequency
# ==========================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1-0-1", 2.0), ("1-1-1", 3.0), ("0-0-1", 1.0), ("1-0-0", 1.0),
        ("1+0+1", 2.0), ("0+0+1", 1.0),
        ("OD", 1.0), ("BD", 2.0), ("TDS", 3.0), ("QID", 4.0), ("HS", 1.0),
        ("BID", 2.0), ("TID", 3.0), ("QDS", 4.0), ("STAT", 1.0),
        ("od", 1.0), ("bd", 2.0),
        ("1/2-0-1/2", 1.0), ("0+0+2½", 2.5),
        ("BD x 5 days", 2.0),
    ],
)
def test_doses_per_day(raw: str, expected: float) -> None:
    assert sig.doses_per_day(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["SOS", "PRN", "sos", "0-0-1 HS SOS", "1-0-1 PRN"])
def test_as_needed_has_no_derivable_dose_count(raw: str) -> None:
    """SOS/PRN is uncheckable; returning a number would invent one."""
    assert sig.doses_per_day(raw) is None


def test_unknown_frequency_is_none() -> None:
    assert sig.doses_per_day("whenever") is None
    assert sig.doses_per_day(None) is None
    assert sig.doses_per_day("") is None


# ==========================================================================
# sig — duration
# ==========================================================================


def test_days_per_month_is_an_explicit_constant() -> None:
    """The 30-day month is an assumption; it must be visible and pinned."""
    assert sig.DAYS_PER_MONTH == 30
    assert sig.DAYS_PER_WEEK == 7


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("x 5 days", 5), ("x5d", 5), ("X5D", 5), ("5d", 5), ("5 days", 5),
        ("x 10d", 10), ("2 weeks", 14), ("3 wks", 21), ("x 1week", 7),
        ("1 month", 30), ("4 months", 120), ("6 months", 180),
        ("5/7", 5), ("2/52", 14), ("1/12", 30),
    ],
)
def test_duration_to_days(raw: str, expected: int) -> None:
    assert sig.duration_to_days(raw) == expected


def test_four_months_uses_the_declared_constant() -> None:
    assert sig.duration_to_days("4 months") == 4 * sig.DAYS_PER_MONTH


@pytest.mark.parametrize("raw", [None, "", "চলবে", "continue", "10", "as directed"])
def test_unparseable_duration_is_none_not_a_guess(raw: str | None) -> None:
    """Bengali words other than the three duration units stay unparsed."""
    assert sig.duration_to_days(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("৪ মাস", 120),      # 4 months
        ("৭ সপ্তাহ", 49),     # 7 weeks
        ("৪৫ দিন", 45),      # 45 days
        ("১ মাস", 30),       # 1 month
        ("৬ মাস", 180),      # 6 months
        ("২ সপ্তাহ", 14),     # 2 weeks
    ],
)
def test_bengali_durations(raw: str, expected: int) -> None:
    """Narrow, deliberate exception to the English/Latin scope.

    Exists so the quantity rules can be exercised against the real samples,
    two of which state their course length in Bengali.
    """
    assert sig.duration_to_days(raw) == expected


def test_bengali_transliteration_is_confined_to_digits_and_duration_words() -> None:
    convert = sig.transliterate_bengali_duration
    assert convert("৪ মাস") == "4 MONTHS"
    assert convert("৪৫ দিন") == "45 DAYS"
    # Any other Bengali text passes through untouched and stays unparseable.
    assert sig.duration_to_days("খাওয়ার পর") is None


# ==========================================================================
# sig — expected quantity
# ==========================================================================


def test_expected_quantity_basic() -> None:
    assert sig.expected_quantity(2.0, 5, 1.0) == 10.0
    assert sig.expected_quantity(3.0, 5, 1.0) == 15.0
    assert sig.expected_quantity(1.0, 30, 2.0) == 60.0


def test_expected_quantity_end_to_end_from_strings() -> None:
    """'Cap Augmentin 625 BD x 5/7' should expect ten capsules."""
    doses = sig.doses_per_day("BD")
    days = sig.duration_to_days("5/7")
    assert sig.expected_quantity(doses, days, 1.0) == 10.0


@pytest.mark.parametrize(
    ("doses", "days", "dose"),
    [(None, 5, 1.0), (2.0, None, 1.0), (2.0, 5, None), (0.0, 5, 1.0), (2.0, 0, 1.0)],
)
def test_expected_quantity_returns_none_on_any_missing_input(
    doses: float | None, days: int | None, dose: float | None
) -> None:
    assert sig.expected_quantity(doses, days, dose) is None


def test_sos_prescription_has_no_expected_quantity() -> None:
    """An as-needed drug cannot have a quantity expectation."""
    assert sig.expected_quantity(sig.doses_per_day("SOS"), 10, 1.0) is None


def test_pack_aware_quantity_reasoning() -> None:
    """A bill line of 2 against a 10'S pack is twenty units, not two.

    This is the arithmetic QUANTITY_SHORT depends on.
    """
    pack = units.parse_pack_size("10'S")
    assert pack is not None and pack.units_per_pack == 10
    billed_packs = 2.0
    assert billed_packs * pack.units_per_pack == 20.0
    expected = sig.expected_quantity(sig.doses_per_day("1-0-1"), sig.duration_to_days("x 10d"), 1.0)
    assert expected == 20.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1 — 0 — 1", 2.0),   # em-dash, as transcribed from a real prescription
        ("1 – 0 – 1", 2.0),   # en-dash
        ("1 − 0 − 1", 2.0),   # minus sign
        ("1 ‑ 0 ‑ 1", 2.0),   # non-breaking hyphen
        ("1 — 0 — 0", 1.0),
    ],
)
def test_dash_variants_in_positional_frequency(raw: str, expected: float) -> None:
    """Prescriptions are written with long dashes and transcribed verbatim.

    Folding them here keeps the verbatim rule intact while letting the parser
    read the schedule. Without this, doses_per_day returns None and every
    quantity rule silently skips.
    """
    assert sig.doses_per_day(raw) == pytest.approx(expected)


def test_dash_variants_do_not_break_duration() -> None:
    """Duration text with a long dash before the count still parses."""
    assert sig.duration_to_days("x 5 days") == 5
    assert sig.duration_to_days("— x 5 days") == 5
