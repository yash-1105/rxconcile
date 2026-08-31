"""Seed reproducible scan history for a demo.

A history check with no history demonstrates as "could not run", which is
honest but shows nothing. These six records give each check something real to
find, and are keyed to the Sri Balaji bill so that re-running it produces a
genuine DUPLICATE_BILL rather than a contrived one.

Reproducible on purpose: ``make seed-demo`` clears the seeded rows and writes
them again, so a run-through can be reset between takes. Only rows this script
wrote are removed -- scans made during a demo survive unless --reset-all is
given.

    make seed-demo
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from typing import Any

from sqlmodel import Session, col, delete, select

from rxconcile.store import ScanRecord, summarise
from rxconcile.store.db import engine

#: Written into every seeded record so they can be found and removed again.
SEED_MARKER: str = "[seeded]"

EMPLOYEE = ("employee@gmail.com", "Yash", "EMP-4417", "employee")
ADMIN = ("admin@gmail.com", "Ishan", "ADM-0001", "admin")

#: The bill the demo re-runs. Matching these makes the duplicate real.
SRI_BALAJI = "SRI BALAJI MEDICALS & DIAGNOSTICS"
SRI_BALAJI_LICENCE = "TN/2019/337821"
PATIENT = "Anil Deshmukh"


def item(item_id: str, name: str, total: str, *, salt: str | None = None,
         days: int | None = None) -> dict[str, Any]:
    return {
        "item_id": item_id, "raw_text": name, "drug_name": name, "salt": salt,
        "strength_value": None, "strength_unit": None, "form": "tablet",
        "quantity": 30.0, "pack_size": None, "units_basis": None,
        "unit_price": None, "discount": None, "line_total": total,
        "batch_no": None, "hsn_code": None, "bbox": None, "agreement": None,
        "confidence": 0.9, "duration_days": days,
    }


def rx_item(item_id: str, name: str, salt: str, days: int | None) -> dict[str, Any]:
    return {
        "item_id": item_id, "raw_text": name, "drug_name": name, "salt": salt,
        "strength_value": None, "strength_unit": None, "form": "tablet",
        "dose_per_administration": 1.0, "frequency_raw": "1-0-0",
        "duration_raw": f"x {days} days" if days else None, "duration_days": days,
        "route": "oral", "instructions": None, "bbox": None, "agreement": None,
        "confidence": 0.9,
    }


def test_line(item_id: str, name: str, total: str) -> dict[str, Any]:
    return {
        "item_id": item_id, "raw_text": name, "test_name": name, "panel": None,
        "quantity": 1.0, "unit_price": total, "line_total": total,
        "bbox": None, "agreement": None, "confidence": 0.9,
    }


def result(
    *,
    pharmacy: str,
    licence: str | None,
    bill_no: str | None,
    bill_date: str,
    patient: str,
    billed: list[dict[str, Any]],
    prescribed: list[dict[str, Any]],
    grand_total: str,
    billed_tests: list[dict[str, Any]] | None = None,
    verdict: str = "match_with_warnings",
) -> dict[str, Any]:
    canonical = [
        {"item_id": line["item_id"], "side": "prescription", "name": line["drug_name"],
         "salt": line["salt"], "match_score": 100.0, "method": "exact"}
        for line in prescribed
    ]
    return {
        "verdict": verdict,
        "score": 84.0,
        "findings": [],
        "matched_pairs": [],
        "unmatched_prescribed": [],
        "unmatched_billed": [],
        "canonical": canonical,
        "matched_tests": [],
        "unmatched_prescribed_tests": [],
        "unmatched_billed_tests": [],
        "prescription": {
            "patient_name": patient, "date_issued": bill_date,
            "items": prescribed, "tests": [], "investigations_present": False,
            "overall_legibility": 0.93, "run_item_counts": [3, 3, 3],
            "unstable_lines": [], "warnings": [],
        },
        "bill": {
            "pharmacy_name": pharmacy, "pharmacy_licence_no": licence,
            "gstin": "33AACCS7781K1ZY", "pharmacy_address": "Chennai, Tamil Nadu",
            "bill_no": bill_no, "bill_date": bill_date, "patient_name": patient,
            "items": billed, "tests": billed_tests or [], "subtotal": grand_total,
            "discount_total": None, "tax_total": None, "grand_total": grand_total,
            "currency": "INR", "run_item_counts": [3, 3, 3],
            "unstable_lines": [], "warnings": [],
        },
        "processing_ms": 18400,
        "reimbursement": {
            "eligible_total": grand_total, "eligible_line_count": len(billed),
            "not_eligible_total": "0", "not_eligible_line_count": 0,
            "needs_review_total": "0", "needs_review_line_count": 0,
            "non_medicine_total": "0", "non_medicine_line_count": 0,
            "lines_without_amount": 0, "currency": "INR", "lines": [],
        },
    }


#: The six records, each with a reason to exist.
def build() -> list[tuple[tuple[str, str, str, str], int, str, dict[str, Any]]]:
    sri_lines = [
        item("bill-01", "TELMA", "267.00"), item("bill-02", "GLYCOMET", "192.00"),
        item("bill-03", "ASPIRIN", "33.00"), item("bill-04", "PANTOCID", "98.00"),
        item("bill-05", "ZINCOVIT", "64.50"), item("bill-06", "RANTAC", "48.00"),
    ]
    sri_rx = [
        rx_item("rx-01", "Telma", "Telmisartan", 30),
        rx_item("rx-02", "Glycomet", "Metformin", 30),
        rx_item("rx-03", "Ecosprin", "Aspirin", 30),
    ]
    return [
        # 1. Makes re-running the Sri Balaji bill a genuine DUPLICATE_BILL:
        #    same bill number, same pharmacy, IDENTICAL lines and total. The
        #    lab section has to be here too -- without it the bills differ, and
        #    the check correctly reports a resubmission instead.
        (EMPLOYEE, 9, "duplicate source", result(
            pharmacy=SRI_BALAJI, licence=SRI_BALAJI_LICENCE, bill_no="8842",
            bill_date="2026-08-23", patient=PATIENT, billed=sri_lines,
            billed_tests=[
                test_line("billtest-01", "Lipid Profile — Total Cholesterol", "180.00"),
                test_line("billtest-02", "Lipid Profile — HDL", "90.00"),
                test_line("billtest-03", "Lipid Profile — LDL", "90.00"),
                test_line("billtest-04", "Lipid Profile — Triglycerides", "90.00"),
                test_line("billtest-05", "Thyroid Profile (T3, T4, TSH)", "450.00"),
            ],
            prescribed=sri_rx, grand_total="1996.40",
        )),
        # 2. A corrected re-issue: same pharmacy and date, one line removed and
        #    a different total. Must read as POSSIBLE_RESUBMISSION, never fraud.
        (EMPLOYEE, 8, "corrected re-issue", result(
            pharmacy="APOLLO PHARMACY, T NAGAR", licence="TN/2018/119045",
            bill_no="5521", bill_date="2026-08-12", patient="Meera Iyer",
            billed=[item("bill-01", "AUGMENTIN", "410.00"),
                    item("bill-02", "DOLO", "31.00")],
            prescribed=[rx_item("rx-01", "Augmentin", "Amoxicillin+Clavulanic Acid", 7)],
            grand_total="441.00",
        )),
        # 3. An earlier claim for Telmisartan on a 30-day course. Re-running the
        #    Sri Balaji bill 9 days later triggers EARLY_REPEAT on that salt.
        (EMPLOYEE, 9, "early repeat source", result(
            pharmacy="MEDPLUS, ADYAR", licence="TN/2020/551200", bill_no="7731",
            bill_date="2026-08-14", patient=PATIENT,
            billed=[item("bill-01", "TELMA 20", "267.00")],
            prescribed=[rx_item("rx-01", "Telma", "Telmisartan", 30)],
            grand_total="267.00",
        )),
        # 4. The same pharmacy carrying a DIFFERENT drug licence number. Filed
        #    under the employee so the whole set is visible in one signed-in
        #    session; an admin sees it either way.
        (EMPLOYEE, 21, "licence conflict", result(
            pharmacy=SRI_BALAJI, licence="TN/2016/884413", bill_no="6104",
            bill_date="2026-07-30", patient="Kavya Rao",
            billed=[item("bill-01", "MONTAIR-LC", "180.00")],
            prescribed=[rx_item("rx-01", "Montair-LC", "Montelukast+Levocetirizine", 10)],
            grand_total="180.00",
        )),
        # 5-6. Ordinary history, so the duplicate check has enough behind it to
        #      mean something rather than reporting thin-history.
        (ADMIN, 34, "routine", result(
            pharmacy="WELLNESS FOREVER, VELACHERY", licence="TN/2021/667001",
            bill_no="2290", bill_date="2026-07-18", patient="Rahul Menon",
            billed=[item("bill-01", "PAN-D", "142.00")],
            prescribed=[rx_item("rx-01", "Pan-D", "Pantoprazole+Domperidone", 14)],
            grand_total="142.00",
        )),
        (EMPLOYEE, 41, "routine", result(
            pharmacy="NETMEDS, ANNA NAGAR", licence="TN/2017/443980",
            bill_no="1187", bill_date="2026-07-11", patient="Sana Qureshi",
            billed=[item("bill-01", "ZERODOL-SP", "96.00")],
            prescribed=[rx_item("rx-01", "Zerodol-SP", "Aceclofenac+Paracetamol", 5)],
            grand_total="96.00",
        )),
    ]


def seed(session: Session, *, today: dt.date) -> int:
    written = 0
    for (email, name, number, role), days_ago, why, payload in build():
        record = ScanRecord(
            created_at=dt.datetime.combine(
                today - dt.timedelta(days=days_ago), dt.time(10, 30)
            ),
            employee_name=name,
            employee_number=number,
            user_email=email,
            role=role,
            prescription_filename=f"{SEED_MARKER} {why} rx.png",
            bill_filename=f"{SEED_MARKER} {why} bill.png",
            verdict=str(payload["verdict"]),
            result_json=json.dumps(payload),
            processing_ms=int(payload["processing_ms"]),
            extraction_runs=3,
            **summarise(payload),
        )
        session.add(record)
        written += 1
    session.commit()
    return written


def clear(session: Session, *, everything: bool) -> int:
    if everything:
        removed = len(list(session.exec(select(ScanRecord)).all()))
        session.exec(delete(ScanRecord))
        session.commit()
        return removed
    seeded = list(
        session.exec(
            select(ScanRecord).where(col(ScanRecord.bill_filename).contains(SEED_MARKER))
        ).all()
    )
    for record in seeded:
        session.delete(record)
    session.commit()
    return len(seeded)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset-all", action="store_true",
                        help="Remove every scan, not only the seeded ones.")
    parser.add_argument("--clear", action="store_true",
                        help="Remove seeded scans and write nothing back.")
    args = parser.parse_args()

    with Session(engine()) as session:
        removed = clear(session, everything=args.reset_all)
        print(f"removed {removed} scan(s)")
        if args.clear:
            return 0
        written = seed(session, today=dt.date.today())
        print(f"seeded {written} scan(s)\n")

    print("What this gives the demo:")
    print("  - re-running the Sri Balaji bill now reports DUPLICATE_BILL (scan #1)")
    print("  - an Apollo re-issue differs, so it reports POSSIBLE_RESUBMISSION")
    print("  - Telmisartan was claimed 9 days ago on a 30-day course: EARLY_REPEAT")
    print("  - Sri Balaji appears with two licence numbers: LICENCE_INCONSISTENT")
    print("\nReset between takes with: make seed-demo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
