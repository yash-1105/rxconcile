#!/usr/bin/env python
"""Generate the synthetic sample pairs used as regression coverage.

These are **not** demonstrations -- the real photographs in ``samples/`` already
demonstrate. Their job is to hold ground that has broken before.

Notation is varied on purpose. Every earlier fixture used ASCII hyphens, so a
sig parser that could not read ``1 — 0 — 1`` shipped all the way to the UI before
anyone noticed: the em-dash silently returned no doses per day, which silently
skipped every quantity rule. Each pair here therefore mixes dash forms, duration
notations, pack notations and letter case.

Real handwriting is materially harder than any of this. A synthetic page renders
glyphs cleanly; a real script does not.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from PIL import Image, ImageDraw, ImageFont

SAMPLES_DIR: Final[Path] = Path(__file__).resolve().parents[2] / "samples"

#: Preferred script-like faces, most handwriting-like first.
_HAND_FONTS: Final[tuple[str, ...]] = (
    "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",
    "/System/Library/Fonts/Noteworthy.ttc",
    "/System/Library/Fonts/Supplemental/Chalkboard.ttc",
    "/System/Library/Fonts/Supplemental/Arial Italic.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
)
_PRINT_FONTS: Final[tuple[str, ...]] = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)
_BOLD_FONTS: Final[tuple[str, ...]] = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)


def _load(paths: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def hand(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return _load(_HAND_FONTS, size)


def printed(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return _load(_BOLD_FONTS if bold else _PRINT_FONTS, size)


def _prescription(
    *,
    clinic: str,
    doctor: str,
    reg_no: str,
    patient: str,
    age: str,
    sex: str,
    date_text: str,
    diagnosis: str,
    lines: list[str],
    path: Path,
) -> None:
    width, height = 1240, 1600
    image = Image.new("RGB", (width, height), "#fefefc")
    draw = ImageDraw.Draw(image)

    draw.rectangle([0, 0, width, 140], fill="#eef2f6")
    draw.text((50, 34), clinic, font=printed(38, bold=True), fill="#16334d")
    draw.text((50, 88), f"{doctor}  |  Reg. No. {reg_no}", font=printed(22), fill="#33475b")
    draw.line([(40, 152), (width - 40, 152)], fill="#8fa2b3", width=2)

    script = hand(34)
    draw.text((50, 180), f"Patient: {patient}", font=script, fill="#141414")
    draw.text((700, 180), f"Age: {age}", font=script, fill="#141414")
    draw.text((50, 228), f"Sex: {sex}", font=script, fill="#141414")
    draw.text((700, 228), f"Date: {date_text}", font=script, fill="#141414")
    draw.text((50, 282), f"Dx: {diagnosis}", font=script, fill="#141414")

    draw.text((50, 350), "Rx", font=printed(58, bold=True), fill="#16334d")
    y = 430
    for line in lines:
        draw.text((80, y), line, font=script, fill="#141414")
        y += 66

    draw.line([(760, height - 190), (1130, height - 190)], fill="#444", width=2)
    draw.text((840, height - 178), "Signature", font=printed(20), fill="#666")
    image.save(path)
    print(f"  wrote {path.name}  {image.size}")


def _bill(
    *,
    pharmacy: str,
    licence: str,
    bill_no: str,
    date_text: str,
    patient: str,
    qty_header: str,
    rows: list[tuple[str, ...]],
    totals: list[tuple[str, str]],
    path: Path,
) -> None:
    width, height = 1240, 1000
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    draw.text((40, 28), pharmacy, font=printed(34, bold=True), fill="#000")
    draw.text((40, 76), f"D.L. No. {licence}", font=printed(19), fill="#333")
    draw.text((40, 104), f"Bill No: {bill_no}        Date: {date_text}", font=printed(21))
    draw.text((40, 136), f"Patient: {patient}", font=printed(21), fill="#000")
    draw.line([(35, 176), (width - 35, 176)], fill="#000", width=2)

    headers = ["#", "PARTICULARS", "BATCH", "HSN", qty_header, "PACK", "RATE", "AMOUNT"]
    xs = [40, 80, 520, 650, 770, 850, 960, 1080]
    for x, header in zip(xs, headers, strict=True):
        draw.text((x, 190), header, font=printed(18, bold=True), fill="#000")
    draw.line([(35, 216), (width - 35, 216)], fill="#000", width=1)

    y = 232
    for row in rows:
        for x, cell in zip(xs, row, strict=True):
            draw.text((x, y), cell, font=printed(18), fill="#000")
        y += 38
    draw.line([(35, y + 8), (width - 35, y + 8)], fill="#000", width=1)

    y += 28
    for label, value in totals:
        bold = label.lower().startswith("grand")
        draw.text((880, y), label, font=printed(20, bold=bold), fill="#000")
        draw.text((1080, y), value, font=printed(20, bold=bold), fill="#000")
        y += 32
    image.save(path)
    print(f"  wrote {path.name}  {image.size}")


def _lab_bill(
    *,
    lab: str,
    reg: str,
    bill_no: str,
    date_text: str,
    patient: str,
    rows: list[tuple[str, str, str, str]],
    totals: list[tuple[str, str]],
    path: Path,
) -> None:
    """A diagnostic laboratory invoice.

    Deliberately a different document from the pharmacy bill: no batch, no HSN,
    no pack size, and it itemises a panel into its analytes the way a real lab
    does. The point of the sample is that the prescription orders one thing
    ("CBC") and the bill charges for six.
    """
    width, height = 1100, 820
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    draw.text((40, 28), lab, font=printed(34, bold=True), fill="#000")
    draw.text((40, 76), f"Reg. No. {reg}", font=printed(19), fill="#333")
    draw.text((40, 104), f"Invoice: {bill_no}        Date: {date_text}", font=printed(21))
    draw.text((40, 136), f"Patient: {patient}", font=printed(21), fill="#000")
    draw.line([(35, 176), (width - 35, 176)], fill="#000", width=2)

    headers = ["#", "INVESTIGATION", "QTY", "RATE", "AMOUNT"]
    xs = [40, 90, 660, 790, 930]
    for x, header in zip(xs, headers, strict=True):
        draw.text((x, 190), header, font=printed(18, bold=True), fill="#000")
    draw.line([(35, 216), (width - 35, 216)], fill="#000", width=1)

    y = 232
    for index, row in enumerate(rows, start=1):
        for x, cell in zip(xs, (str(index), *row), strict=True):
            draw.text((x, y), cell, font=printed(18), fill="#000")
        y += 38
    draw.line([(35, y + 8), (width - 35, y + 8)], fill="#000", width=1)

    y += 28
    for label, value in totals:
        bold = label.lower().startswith("grand")
        draw.text((760, y), label, font=printed(20, bold=bold), fill="#000")
        draw.text((930, y), value, font=printed(20, bold=bold), fill="#000")
        y += 32
    image.save(path)
    print(f"  wrote {path.name}  {image.size}")


def sample_lab() -> None:
    """A lab bill for p4, whose real handwriting orders "CBC".

    Six of the rows are the analytes a Complete Blood Count decomposes into --
    the case the panel dictionary exists for, and a match, not six findings. The
    seventh is a Lipid Profile nobody ordered, which is a real discrepancy.
    """
    print("sample-lab  panel decomposition against a real prescription (p4)")
    _lab_bill(
        lab="SUNRISE DIAGNOSTIC LABORATORY",
        reg="WB/PATH/2019/4471",
        bill_no="LAB-20881",
        date_text="24-08-2026",
        # Matches the patient on samples/p4.jpg, which this bill is paired with.
        patient="Dalia Kundu",
        rows=[
            ("Haemoglobin", "1", "120.00", "120.00"),
            ("Total WBC Count", "1", "120.00", "120.00"),
            ("RBC Count", "1", "120.00", "120.00"),
            ("Platelet Count", "1", "150.00", "150.00"),
            ("Packed Cell Volume", "1", "110.00", "110.00"),
            ("Differential Count", "1", "140.00", "140.00"),
            ("Lipid Profile", "1", "800.00", "800.00"),
        ],
        totals=[("Sub Total", "1560.00"), ("Grand Total", "1560.00")],
        path=SAMPLES_DIR / "sample-lab-bill.png",
    )


def sample_01() -> None:
    """Clean matching pair. Em-dashes, 'x 5 days', pack \"10'S\"."""
    print("sample-01  clean match")
    _prescription(
        clinic="MERIDIAN FAMILY CLINIC",
        doctor="Dr. P. Raghavan, MBBS",
        reg_no="MMC-30417",
        patient="A. Kulkarni",
        age="41 years",
        sex="M",
        date_text="14-03-2026",
        diagnosis="Acute bacterial pharyngitis",
        lines=[
            "1)  Tab. AZITHRAL 500mg    1 — 0 — 0   x 3 days",
            "2)  Tab. Dolo 650          1 — 0 — 1   x 5 days",
            "3)  Cap. Pan 40mg          1 — 0 — 0   x 5 days",
        ],
        path=SAMPLES_DIR / "sample-01-prescription.png",
    )
    _bill(
        pharmacy="MERIDIAN PHARMACY",
        licence="MH-14C-77120",
        bill_no="MP-2026-0431",
        date_text="14-03-2026",
        patient="A. Kulkarni",
        qty_header="QTY",
        rows=[
            ("1", "AZITHRAL 500MG TAB", "AZ4410", "30042019", "3", "10'S", "31.00", "93.00"),
            ("2", "DOLO 650 TAB", "DL7781", "30049099", "10", "10'S", "2.20", "22.00"),
            ("3", "PAN 40MG CAP", "PN2205", "30049099", "5", "10'S", "9.80", "49.00"),
        ],
        totals=[("Subtotal", "164.00"), ("CGST", "9.84"), ("SGST", "9.84"),
                ("Grand Total", "183.68")],
        path=SAMPLES_DIR / "sample-01-bill.png",
    )


def sample_02() -> None:
    """Strength mismatch plus an unprescribed antibiotic. En-dashes, '5/7', '1x10'."""
    print("sample-02  strength mismatch + unprescribed antibiotic")
    _prescription(
        clinic="RIVERSIDE MEDICAL CENTRE",
        doctor="Dr. N. Bhatt, MD",
        reg_no="GMC-88214",
        patient="S. Desai",
        age="58 years",
        sex="F",
        date_text="22-03-2026",
        diagnosis="Hypertension; gastritis",
        lines=[
            "1)  Tab. TELMA 40mg        1 – 0 – 0   x 30 days",
            "2)  Tab. rabium 20mg       1 – 0 – 1   5/7",
        ],
        path=SAMPLES_DIR / "sample-02-prescription.png",
    )
    _bill(
        pharmacy="RIVERSIDE CHEMISTS",
        licence="GJ-08D-31904",
        bill_no="RC-2026-1188",
        date_text="22-03-2026",
        patient="S. Desai",
        qty_header="QTY",
        rows=[
            ("1", "TELMA 80MG TAB", "TL9902", "30049099", "3", "1x10", "12.40", "372.00"),
            ("2", "RABIUM 20MG TAB", "RB3320", "30049099", "1", "1x10", "8.60", "86.00"),
            ("3", "LEVOFLOX 500MG TAB", "LV6611", "30042019", "1", "1x10", "24.00", "240.00"),
        ],
        totals=[("Subtotal", "698.00"), ("CGST", "41.88"), ("SGST", "41.88"),
                ("Grand Total", "781.76")],
        path=SAMPLES_DIR / "sample-02-bill.png",
    )


def sample_03() -> None:
    """Brand substitution: Dolo dispensed as CALPOL, Pan as pantocid."""
    print("sample-03  brand substitution")
    _prescription(
        clinic="ASHOKA POLYCLINIC",
        doctor="Dr. V. Menon, MBBS DNB",
        reg_no="KMC-51228",
        patient="R. Pillai",
        age="6 years",
        sex="M",
        date_text="02-04-2026",
        diagnosis="Viral fever",
        lines=[
            "1)  Tab. DOLO 650          1 — 0 — 1   x 4 days",
            "2)  Cap. Pan 40mg          1 - 0 - 0   x 4 days",
        ],
        path=SAMPLES_DIR / "sample-03-prescription.png",
    )
    _bill(
        pharmacy="ASHOKA MEDICALS",
        licence="KL-11A-60233",
        bill_no="AM-2026-0902",
        date_text="02-04-2026",
        patient="R. Pillai",
        qty_header="NOS",
        rows=[
            ("1", "CALPOL 650MG TAB", "CP1180", "30049099", "8", "STRIP OF 10", "2.60", "20.80"),
            ("2", "pantocid 40mg tab", "PC7742", "30049099", "4", "STRIP OF 10", "9.20", "36.80"),
        ],
        totals=[("Subtotal", "57.60"), ("CGST", "3.46"), ("SGST", "3.46"),
                ("Grand Total", "64.52")],
        path=SAMPLES_DIR / "sample-03-bill.png",
    )


def main() -> int:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    chosen = next((p for p in _HAND_FONTS if Path(p).exists()), "PIL default")
    print(f"script face: {chosen}\n")
    sample_01()
    sample_02()
    sample_03()
    sample_lab()
    print("\nNote: synthetic pages render glyphs cleanly. Real handwriting is")
    print("materially harder and these samples do not evidence accuracy on it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
