"""Extraction prompts. Text only -- no logic lives here, so tuning is safe.

``PROMPT_VERSION`` participates in the cache key. Bump it whenever a prompt
changes, otherwise cached results from the previous wording are served and
prompt edits appear to do nothing.
"""

from __future__ import annotations

from typing import Final

PROMPT_VERSION: Final[str] = "2026-08-27.1"

_NEVER_GUESS: Final[str] = """
ABSOLUTE RULE — NEVER INVENT A VALUE
If a field is not confidently legible, return null for it. Do not guess, do not
autocorrect to the nearest real drug, do not complete a partial word, and do not
infer a value from context or from what would be medically plausible.

A null is a CORRECT answer. A confident wrong drug name is the single worst
failure this system can produce. When torn between a plausible reading and null,
return null.

Never let plausibility influence a reading. You are transcribing handwriting,
not diagnosing. Report only what is on the page.
""".strip()

_CONFIDENCE: Final[str] = """
CONFIDENCE
Every item carries `confidence` from 0 to 1 measuring HANDWRITING LEGIBILITY
ONLY — how clearly the characters are formed and how certain you are of the
glyphs. It must NOT reflect how plausible, common, or medically sensible the
reading is. A crisply written unusual drug scores high. A famous drug in an
unreadable scrawl scores low.
""".strip()

_VERBATIM: Final[str] = r"""
VERBATIM TRANSCRIPTION
`raw_text` is the line copied EXACTLY as written, preserving original spelling,
abbreviations, capitalisation, spacing and script. Mark each illegible portion
with [?]. Do not expand abbreviations, correct spelling, or tidy the text in
`raw_text` — structured fields are where interpretation belongs.
""".strip()

_DATES: Final[str] = """
DATES
Return the date EXACTLY as written in `date_issued_raw` (or `bill_date_raw`),
e.g. "03/04/26", "3rd Apr 26". Do NOT reformat it, do NOT convert it to ISO, and
do NOT decide whether it is day-first or month-first. Software resolves the date
afterwards and deliberately rejects ambiguous ones. If no date is visible,
return null.
""".strip()

SIG_NOTATION: Final[str] = """
INDIAN SIG NOTATION — read these correctly
Positional dosing, in the order morning-afternoon-night:
  "1-0-1"   one in the morning, none at midday, one at night
  "1-1-1"   one morning, one midday, one night
  "0-0-1"   one at night only
  "1/2-0-1/2"  half a tablet morning and night
Latin abbreviations:
  OD    once daily            BD    twice daily
  TDS   three times daily     QID   four times daily
  HS    at bedtime            SOS   as needed
  STAT  immediately, once     AC    before food
  PC    after food            PRN   as needed
Duration:
  "x 5 days", "x5d", "5/7"    a five-day course ("5/7" means 5 days of a 7-day
                              week, NOT the fifth of July)
  "1/12"                      one month     "2/52"   two weeks
Put the frequency VERBATIM in `frequency_raw` ("1-0-1", "BD"). Put the course
length in `duration_days` as an integer ONLY when stated; otherwise null.
""".strip()

PRESCRIPTION_INSTRUCTION: Final[str] = f"""
You are reading a photograph of a HANDWRITTEN INDIAN MEDICAL PRESCRIPTION.
Expect cursive English, heavy abbreviation, Latin sig notation, and sometimes
Devanagari or another Indian script mixed into the same page. Expect a
letterhead, a patient block, a Rx symbol, a list of drugs, and a signature.

Extract every prescribed line in DOCUMENT ORDER, top to bottom.

{_NEVER_GUESS}

Specifically for drug names: if you cannot confidently read the drug name, set
`drug_name` to null and still return the line with its `raw_text`. A line with
`raw_text` filled and `drug_name` null is a useful, correct result.

{_VERBATIM}

{SIG_NOTATION}

{_CONFIDENCE}

PATIENT AGE
Return `patient_age` VERBATIM INCLUDING ITS UNIT: "6 months", "34 years", "45Y",
"2 1/2 yrs". Never convert to a bare number and never assume years — the
difference between 6 months and 6 years is critical information.

{_DATES}

WARNINGS
Use `warnings` for page-level problems: an illegible signature block, a missing
prescriber registration number, a cropped or cut-off page, items you suspect
exist but cannot read at all.

`overall_legibility` scores the handwriting quality of the whole page, 0 to 1.

Do not include any commentary, diagnosis, dosing advice, or clinical opinion
anywhere in the output. Transcribe and structure only.
""".strip()

BILL_INSTRUCTION: Final[str] = f"""
You are reading a photograph of an INDIAN PHARMACY INVOICE (a retail chemist's
bill). These are usually printed or dot-matrix, in a table whose columns are
some subset of:

  item / particulars / description   the product name, often with pack size
  batch / B.No                       batch number
  exp / expiry                       expiry date
  MRP                                maximum retail price
  qty                                quantity dispensed
  rate                               price per unit charged
  disc / discount                    line discount
  GST% / CGST / SGST / IGST          tax rate or amount
  HSN                                HSN tax classification code
  amount / net amount                the line total

Extract EVERY line item in PRINTED ORDER, including lines that are not
medicines — surgical items, consumables, delivery or service charges. Give
non-medicine lines `form` = "other". Do not silently skip a line because it is
not a drug; an unexpected charge is exactly what reconciliation must surface.

{_NEVER_GUESS}

{_VERBATIM}

PACK SIZE
Return `pack_size` EXACTLY as printed — "10'S", "1x10", "15ML", "STRIP OF 10".
Do not parse it, convert it, or reduce it to a number.

MONEY
Return `unit_price`, `line_total`, `subtotal`, `tax_total` and `grand_total` as
JSON NUMBERS, without currency symbols or digit grouping: 1200.00, never
"Rs. 1,200.00". `line_total` is the NET amount for the line, after discount.
`tax_total` is the total tax on the invoice. `grand_total` is the net payable.
If a value is not printed, return null rather than computing it yourself.

{_DATES}

{_CONFIDENCE}

Return `currency` as an ISO code — "INR" for a rupee bill.

Do not include commentary, clinical opinion, or advice anywhere in the output.
""".strip()


def schema_retry_suffix(error: str) -> str:
    """Text appended to the prompt for the single retry after a schema failure."""
    return f"""

--- RETRY: YOUR PREVIOUS RESPONSE FAILED VALIDATION ---
The JSON you returned did not conform to the required schema. The validator
reported:

{error}

Return the COMPLETE object again, corrected, conforming exactly to the schema.
Do not invent values to satisfy a field — a field you cannot read is null. Do
not include any field that is not in the schema. Return JSON only.
""".rstrip()
