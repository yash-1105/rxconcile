"""Extraction prompts. Text only -- no logic lives here, so tuning is safe.

``PROMPT_VERSION`` participates in the cache key. Bump it whenever a prompt
changes, otherwise cached results from the previous wording are served and
prompt edits appear to do nothing.
"""

from __future__ import annotations

from typing import Final

PROMPT_VERSION: Final[str] = "2026-09-02.1"

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

_STRENGTH_UNIT: Final[str] = """
STRENGTH AND ITS UNIT — DO NOT SUPPLY A UNIT THAT IS NOT WRITTEN
`strength_unit` is transcription, not knowledge. Return a unit ONLY if that unit
is physically written on the page next to the number.

If the page says "THYROX 50", the unit is NOT written. Return
strength_value = 50 and strength_unit = null. Do not supply "mcg" because you
know Thyrox is dosed in micrograms.
If the page says "DEFROL 40000", return strength_unit = null. Do not supply
"IU".
If the page says "ESIPRAM 5", return strength_unit = null. Do not supply "mg".
If the page says "PARACETAMOL 500mg", the unit IS written: return "mg".

A correct unit that you inferred rather than read is still a fabrication. Null is
the correct answer whenever the unit is absent from the page.
""".strip()

_BBOX: Final[str] = """
BOUNDING BOX
For each item return `bbox` as [x0, y0, x1, y1], normalised 0-1 against the image
width and height, tightly enclosing the WHOLE line as written -- drug name,
strength, frequency and duration together, not just the drug name. x0,y0 is the
top-left corner and x1,y1 the bottom-right, so x1 > x0 and y1 > y0.

If you cannot confidently locate the line on the page, return null. **Do not
guess a box.** A box in the wrong place is worse than no box: it points a
reviewer at the wrong line and invites them to confirm a reading they never
actually checked.
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
DATES — WHICH DATE, AND WHAT TO DO WITH IT
Choose the date SOURCE in this priority order, and stop at the first one present:
  1. A PRINTED, LABELLED form field — "Date:", "Appointment Date:", "Bill Date:".
     A printed label with a handwritten value still counts as a labelled field.
  2. A date in the letterhead or header block.
  3. A date appearing loose in body or advice text.
Never take a date from body text when a labelled field exists. A date inside
advice such as "review on 3/3/21" or "give next dose on 10/2/21" is a future
appointment, NOT the date this document was issued — prefer the labelled field.

Return the chosen date EXACTLY as written in `date_issued_raw` (or
`bill_date_raw`), e.g. "03/04/26", "19-02-2021". Do NOT reformat it, do NOT
convert it to ISO, and do NOT decide whether it is day-first or month-first.
Software resolves the date afterwards and deliberately rejects ambiguous ones.
If no date is visible anywhere, return null.
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

_DURATION: Final[str] = """
DURATION — TRANSCRIBE, DO NOT CONVERT
`duration_raw` is the course length EXACTLY as written, in the original script:
"x 5 days", "x5d", "5/7", "1/12", "৪ মাস", "২ সপ্তাহ". Copy it; never translate,
normalise or convert it.

`duration_days` is filled ONLY when the page states a plain number of DAYS.
  "x 5 days"  -> duration_raw "x 5 days", duration_days 5
  "5/7"       -> duration_raw "5/7",      duration_days 5   (5 days of a 7-day week)
  "২ সপ্তাহ"    -> duration_raw "২ সপ্তাহ",  duration_days NULL  (weeks, not days)
  "৪ মাস"      -> duration_raw "৪ মাস",    duration_days NULL  (months, not days)
  "1/12"      -> duration_raw "1/12",     duration_days NULL  (one month)
  "চলবে"       -> duration_raw "চলবে",     duration_days NULL  (continue; no length)

Never multiply months or weeks into days. A month is not 30 days on this page —
the page does not say how long a month is. Software converts durations later,
where the assumption is visible and testable. Leaving duration_days null is
always correct when the page does not state days.
""".strip()

_INVESTIGATIONS: Final[str] = """
INVESTIGATIONS (LAB TESTS)
Indian prescriptions order lab work in a short block headed "Adv:", "Advice:",
"Inv:", "Investigations:", "Lab:", or simply written above the drug list. Put
every ordered test in `tests`, in document order, separate from `items`.

Tests are usually ordered by ABBREVIATED PANEL NAME: LFT, KFT, RFT, CBC, TFT,
Lipid Profile, HbA1c, Urine R/M, USG, X-Ray, ECG. **Copy the abbreviation as
written.** Do not expand "LFT" into its component tests and do not list the
analytes a panel contains -- software does that decomposition, and it needs to
know what the doctor actually wrote.

One written line is one entry. If the page reads "CBC, LFT, RBS", that is three
entries, because they are three orders.

`investigations_present` is a question about the LAYOUT OF THE PAGE, not about
what you could read:
  - true  -- there IS such a section, EVEN IF every line in it is illegible
  - false -- you can see the page and there is no investigations section
  - null  -- you cannot tell (page cropped, region obscured)
An unreadable tests section is `investigations_present` = true with entries
whose `test_name` is null and whose `raw_text` holds whatever you could see.
**Returning an empty `tests` list with `investigations_present` = false when the
section is merely unreadable is a serious error**: it reports "no tests ordered"
when the truth is "tests ordered, could not read them". Those are different
answers and only one of them is clean.
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

{_DURATION}

{_INVESTIGATIONS}

{_STRENGTH_UNIT}

{_CONFIDENCE}

{_BBOX}

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

{_STRENGTH_UNIT}

WHAT THE QUANTITY COLUMN COUNTS
Set `units_basis` ONLY when the bill states it explicitly:
  - "pack"  when the column is headed "Strips", "Packs", "Qty (strips)", or the
            row otherwise makes clear the count is of whole packs
  - "unit"  when the column is headed "Units", "Nos", "Tabs", or the quantity
            obviously exceeds any plausible number of packs (e.g. qty 30 against
            a pack of 10'S on a one-month course)
Otherwise return null. **Do not guess.** A wrong basis multiplies or divides the
dispensed quantity by the pack size, which is worse than leaving it unstated --
software compares both readings when this is null.

{_BBOX}

LAB TEST LINES
Some bills are diagnostic-lab bills, not pharmacy bills, and some are both. Put
every LAB TEST line in `tests` and every MEDICINE or consumable line in `items`.
A bill with only tests, or only medicines, is normal -- prescriptions and lab
orders are commonly billed on separate documents.

Copy each test name as printed. A bill often itemises a panel into its analytes:
"SGPT", "SGOT", "Bilirubin Total", "Alkaline Phosphatase" may be four printed
lines that together are one ordered LFT. **Return the four lines as four
entries.** Do not merge them into a panel and do not add a panel name that is
not printed on the bill -- software reassembles panels, and it needs the printed
lines to do it.

EXPIRY
Return `expiry_raw` EXACTLY as printed for each line, from the Exp or Expiry
column -- "07/2026", "JUL 26", "03-2027". Do not reformat it and do not fill it
in from the batch number or anywhere else. Null when the column is blank or the
bill has no expiry column. Software resolves it and decides what it means; a
tidied-up expiry would defeat that.

PACK SIZE
Return `pack_size` EXACTLY as printed — "10'S", "1x10", "15ML", "STRIP OF 10".
Do not parse it, convert it, or reduce it to a number.

IDENTIFIERS ON THE LETTERHEAD
Return `gstin` exactly as printed, usually labelled GSTIN or GST No. and 15
characters long. **Transcribe it character for character and do not correct it.**
Software verifies its check digit, and a helpful correction would defeat that.
Return `pharmacy_licence_no` (D.L. No., DL No., Drug Licence) as printed, and
`pharmacy_address` as the address block appears. Null for anything not printed.

DISCOUNTS
If the bill has a discount column, return `discount` per line and
`discount_total` for the bill, IN CURRENCY. If there is no discount column,
return null -- **not zero.** Null means the bill does not say; zero means the
bill says nothing was taken off, and software treats those differently when it
checks the arithmetic.

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


#: The one instruction on this prompt that is not about accuracy.
#:
#: A model asked to read a lab report will volunteer that 33.16 is low against a
#: range of 75-250, because that is the helpful thing to do everywhere else. Here
#: it is out of scope (hard rule 10) and would be the system giving medical
#: advice in its own voice, which hard rule 4 forbids outright. So the prohibition
#: is stated in the prompt as well as enforced by the schema having nowhere to put
#: a judgement.
_NO_INTERPRETATION: Final[str] = """
TRANSCRIBE THE RESULTS. DO NOT INTERPRET THEM.

You are a transcriber here, not a clinician. Copy what is printed:

  * Copy the result exactly as text -- "294.00", "<0.01", "Not detected", "1:80".
    Never convert to a number, never round, never normalise a unit.
  * Copy the reference range exactly as printed.
  * Copy a flag ONLY if the laboratory printed one next to the result ("H", "L",
    "High", "*"). If there is no flag on the page, `lab_flag` is null.

**Never derive a flag by comparing the result to the range.** A value outside its
range is still just a number to copy. Do not write "high", "low", "abnormal",
"normal", "deficient" or "borderline" anywhere in the output, in any field,
including `warnings` and `raw_text` -- unless those exact words are themselves
printed on the page, in which case they are part of the transcription.

Do not add commentary, interpretation, diagnosis or advice anywhere.
""".strip()

LAB_REPORT_INSTRUCTION: Final[str] = f"""
You are reading an INDIAN DIAGNOSTIC LABORATORY REPORT. It is usually a
computer-generated PDF, several pages long, laid out as a table whose columns
are some subset of:

  Test Name         the analyte, often indented under a panel heading
  Results           the measured value
  Units             pg/mL, mg/dL, nmol/L
  Bio. Ref. Interval   the reference range

YOU MAY BE SHOWN SEVERAL PAGES AT ONCE. They are one document, in order, page 1
first. Read all of them and return every result line from every page.

Two things follow from that, and they are the reason the pages are sent together:

  * A panel heading printed on one page governs result lines printed on the NEXT
    page. "GLUCOSE FASTING (F) AND POST PRANDIAL (PP)" may head page 4 with
    "Glucose Fasting" on page 4 and "Glucose (PP)" on page 5 -- both belong to
    that panel. Carry the heading across the page break.
  * Report pages repeat the patient header and carry long blocks of notes,
    comments and disclaimers. Those are NOT results. A page consisting only of
    notes is perfectly readable and simply has no results on it -- do not list it
    in `unreadable_pages`.

Record `page` on every result: the 1-based page you read it from.

{_NEVER_GUESS}

{_VERBATIM}

{_NO_INTERPRETATION}

{_DATES}

{_BBOX}

{_CONFIDENCE}

`overall_legibility` scores the whole document, 0 to 1.
""".strip()
