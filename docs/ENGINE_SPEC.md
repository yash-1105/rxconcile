# Reconciliation engine — amended specification

**Status: implemented** in `api/rxconcile/reconcile/engine.py`. Pairing uses
`scipy.optimize.linear_sum_assignment`; `score` is `float | None` with a
validator asserting it is None if and only if the verdict is `inconclusive`.
The open questions at the foot of this document are now settled in the engine:
the pairing algorithm and its weights, `inconclusive` accompanying rather than
suppressing findings, and score derivation.

Amendments below supersede the original engine spec wherever they conflict. Each
comes from measured evidence in [BASELINE.md](./BASELINE.md) and the decisions in
[DESIGN_DECISIONS.md](./DESIGN_DECISIONS.md).

---

## 1. Confidence gating is replaced by agreement gating

`LOW_CONFIDENCE_FIELD` gates on **`agreement < 1.0`**, never on model confidence.

The original threshold of `confidence < 0.6` is withdrawn. Raw model confidence
measured **0.75–0.95 across 56 observations**, never once below 0.6, and was
sometimes *inverted* against reproducibility — the highest score observed (0.95)
sat on a field the model could not reproduce across three runs.

**`confidence` and `overall_legibility` gate nothing.** They are retained on the
models for the record only, and their field docs say so.

## 2. The inconclusive verdict uses agreement

`ILLEGIBLE_RX` and the `inconclusive` verdict are driven by agreement, not by
`overall_legibility` (which never fell below 0.75 on any real sample, so the
original 0.4 floor could never fire).

Verdict is `inconclusive` when **any** of:

- more than half of prescribed items have a null `drug_name` after resolution; **or**
- mean `drug_name` agreement across prescribed items is `< 0.67`; **or**
- item counts were unstable across runs (`len(set(run_item_counts)) > 1`).

## 3. New rule — `ITEM_COUNT_UNSTABLE`

| | |
| --- | --- |
| Rule code | `ITEM_COUNT_UNSTABLE` |
| Severity | **critical** |
| Scope | document-level |
| `prescribed_ref` | **null** |
| `billed_ref` | **null** |

Raised when `run_item_counts` contains more than one distinct value on either
document.

**Both refs must be null.** An intermittent line has no `item_id` in the
canonical extraction, so referencing it makes `ReconciliationResult` fail
construction — the referential-integrity validator requires every ref to name an
existing item. Evidence goes in `detail` instead:

```json
{
  "rule_code": "ITEM_COUNT_UNSTABLE",
  "severity": "critical",
  "prescribed_ref": null,
  "billed_ref": null,
  "detail": {
    "document": "prescription",
    "run_item_counts": [7, 6, 6],
    "unstable_lines": ["- 6# (P+H)"]
  }
}
```

**Why critical.** A line seen in 2 of 3 runs is a silent `RX_NOT_BILLED` false
negative: the engine has nothing to match, raises nothing, and reports a **clean
match on a line nobody saw**. That is worse than a wrong drug name, which at
least appears in the output where a human can catch it. Observed on p4:
`run_item_counts = [7, 6, 6]`, with `- 6# (P+H)` intermittent.

## 4. Typed pairs and identifier references

`matched_pairs` is `list[MatchedPair]`, never `list[tuple[str, str]]`. Every
reference — `prescribed_ref`, `billed_ref`, `unmatched_prescribed`,
`unmatched_billed`, `MatchedPair.prescribed_id`, `MatchedPair.billed_id` — is an
`item_id`, never `raw_text`. Already enforced by the schema validator.

## 5. Quantity rules skip on a null expectation

`duration_days` is now populated by `normalize/sig.py` and is **null far more
often** than the original spec assumed — `চলবে` ("continue") has no course
length, and oncology lines frequently state none. On the real corpus,
`expected_quantity` is computable for only **8 of 18** items.

`QUANTITY_SHORT` and `QUANTITY_EXCESS` **skip silently when
`expected_quantity is None`**. A null duration is not a discrepancy, and firing a
quantity rule against an absent expectation reports a discrepancy against a
number nobody wrote down.

### 5a. Pack/unit basis — resolved by evidence, or not at all

A billed `quantity` may count whole packs or individual units, and nothing in the
extracted data distinguishes them. Multiplying by `units_per_pack` when the bill
already states units inflates by the pack size — the month-to-days assumption
relocated into the engine.

Resolution order:

1. **`BilledItem.units_basis`** (`"pack" | "unit" | None`), set by the extractor
   only when the bill states it explicitly. A declared basis **wins outright**.
2. **Price reconciliation.** If `quantity × units_per_pack × unit_price`
   reconciles to `line_total` while `quantity × unit_price` does not, the rate is
   per dosage unit and the quantity therefore counts packs
   (`method="price_reconciled"`).
3. **Otherwise, decline.** `quantity × unit_price == line_total` is equally
   consistent with units-priced-per-unit and packs-priced-per-pack, so it is
   recorded as `price_inconclusive`, never assumed. Discounts push `line_total`
   below the gross figure, so a mismatch is absence of evidence rather than
   evidence for the other reading.

When the basis is unresolved, the expectation is evaluated under **both**
readings:

- Both readings raise the **same** rule → emit it; the discrepancy is real either
  way.
- Readings disagree → assert nothing, and emit **`QUANTITY_AMBIGUOUS`**
  (severity `info`) carrying both interpretations and `basis_method`.

Because it is `info`, ambiguity never moves the verdict or the score.

Quantity comparison remains pack-aware via `normalize.units.parse_pack_size`, and
skips entirely when it returns `method="unrecognised"`.

## 6. `LOW_CONFIDENCE_FIELD` is per item, not per field

Now that it gates on agreement, this rule is expected to fire **frequently**.

- Emit **one finding per affected item**, not per affected field. An item with
  three shaky fields is one item needing review, not three findings.
- Put the per-field agreement breakdown in `detail`.
- Severity stays **`info`**.

```json
{
  "rule_code": "LOW_CONFIDENCE_FIELD",
  "severity": "info",
  "prescribed_ref": "rx-02",
  "detail": {
    "agreement": {"drug_name": 0.33, "duration_raw": 0.67},
    "min_agreement": 0.33,
    "nulled_fields": ["drug_name"]
  }
}
```

## 7. Review summary

**Implemented.** `ReconciliationResult.review_summary` carries:

| Field | Meaning |
| --- | --- |
| `items_needing_review` | items with any field below full agreement |
| `fields_nulled_by_disagreement` | fields resolved to null because runs disagreed |
| `unstable_line_count` | intermittent lines across both documents |

Two implementation notes for the engine:

- It is **derived during validation from the documents and overrides anything
  supplied**, so the engine does not compute it and cannot let it drift from the
  findings.
- Items whose `agreement` is `None` (single-run extraction) are **not** counted
  as needing review: there is no evidence either way, and counting them would
  overstate what was measured.

---

## Settled in the implementation

- **Pairing**: composite similarity, drug/salt 0.60 + strength 0.25 + form 0.15,
  accepted above 0.55, assigned globally with
  `scipy.optimize.linear_sum_assignment`. An unresolved drug scores 0 on the drug
  component and never falls back to string similarity; a missing strength or form
  scores 0 rather than a free pass. Note 0.55 sits just below the drug weight, so
  a confident drug match pairs two lines on its own — the common case on real
  bills, where the bill states no form.
- **`inconclusive` accompanies findings**, never suppresses them. Findings are
  computed in full and `ILLEGIBLE_RX` is appended carrying the reasons.
- **Score**: `100 − 25×criticals − 8×warnings`, floored at 0, info ignored, and
  `None` under `inconclusive`.

---

## 8. Lab test reconciliation

Engine work of the same size as the medicine engine, reusing its machinery
rather than paralleling it: the same never-guess rule, the same N=3 agreement
resolution, the same `Finding` shape, the same `MatchedPair`, the same
`CHECK_UNAVAILABLE` treatment, and the same verdict and score arithmetic. A
critical test finding is a `mismatch` exactly as a critical medicine finding is.

### 8a. Comparison happens at panel components, not written lines

The two documents describe lab work at different granularities. A doctor writes
`LFT`; the laboratory bills SGPT, SGOT, Bilirubin and Alkaline Phosphatase on
four lines. Compared literally that is one test never performed plus four never
ordered — **five findings against a bill that is correct.**

`normalize/lab_panels.py` maps panel names common in Indian practice (LFT,
KFT/RFT, CBC, Lipid Profile, Thyroid Profile, HbA1c, Urine R/M) to the analytes
a bill itemises them into, and maps analyte aliases (ALT→SGPT, AST→SGOT,
TLC→Total WBC Count, Hb→Haemoglobin) to one canonical name. Both sides are
expanded to component sets and compared as sets.

It carries the same warning as the drug dictionary: **hand-compiled,
unverified, not for clinical use.** Panel composition varies between
laboratories; a production system needs the reporting lab's own test master.

### 8b. Rules

| Code | Severity | Fires when |
| --- | --- | --- |
| `TEST_NOT_BILLED` | critical | an ordered test has no component on the bill |
| `TEST_NOT_PRESCRIBED` | critical | a billed test matches no ordered test or panel |
| `PANEL_PARTIAL` | warning | a panel is billed incompletely; the finding names the missing components |
| `TEST_DUPLICATE` | warning | one test on two lines, or one line with quantity above 1 |
| `TEST_UNRESOLVED` | info | a written name resolves to no known test or panel |

Both criticals downgrade to **warning** whenever the counterpart document
cannot support a confident claim — the same counterpart-confidence rule the
medicine side already applies to `RX_NOT_BILLED`.

### 8c. Absence of tests is not a discrepancy

A document with no tests on either side produces no test findings at all, no
`CHECK_UNAVAILABLE`, and no score penalty. Most prescriptions order only
medicines and must reconcile perfectly clean.

### 8d. Present-but-unreadable is not absent

`tests = []` with `investigations_present = true` means the section exists and
could not be read. Reporting that as "no tests ordered" would make every billed
test unauthorised — one unreadable region becoming a document full of
accusations. Only `investigations_present = false`, a positive observation that
the page carries no such section, licenses a critical. `null` is treated as
uncertain, not as absent.

The extractor is asked for `investigations_present` as a question about
**layout**, answerable even when no word in the section is legible, and Python
forces it true whenever a test line was in fact read.

### 8d-bis. How a laboratory actually prints a line

A bill rarely prints a bare analyte name. Two forms are parsed before lookup,
both without lowering the match threshold:

| Written | Resolves to | Why |
| --- | --- | --- |
| `Thyroid Profile (T3, T4, TSH)` | the **panel** | the parenthetical lists what it contains |
| `Lipid Profile — Total Cholesterol` | the **component** | the bill charged for one analyte |

A component line resolves to the component, never the panel: resolving it to
the panel would let one billed line satisfy an order of six.

Separators handled: em-dash, en-dash, figure dash, minus, colon, pipe, and a
spaced hyphen. A plain unspaced hyphen does not split, so a hyphenated name is
not torn in half. If either side is unrecognisable the line stays **unresolved**
rather than being partially matched.

### 8e. An unresolved panel expands to nothing, not to zero components

An unresolved `LabMatch` carries an empty component tuple, and an empty set
trivially covers nothing — so a naive reading reports every billed component as
unprescribed. One illegible word, four criticals: the same shape as the
unidentifiable-drug false positive fixed earlier.

Resolution is therefore checked before components are used. An unresolved order
is reported as `TEST_UNRESOLVED` plus `CHECK_UNAVAILABLE`, is never compared,
never produces `TEST_NOT_BILLED`, and its presence softens every billed-line
accusation to a warning carrying a stated reason.

### 8e-bis. Derived analytes are not a shortfall

`lab_panels.DERIVED_COMPONENTS` marks analytes a laboratory calculates rather
than assays and bills. VLDL is derived from triglycerides, so a lipid profile
billed as four lines is complete, not partial. Without this a correct bill
raised `PANEL_PARTIAL` for a component no laboratory would ever charge for.

It stays listed in the panel rather than being deleted — a report does show
VLDL, and a bill that itemises it should still count as covering it. This only
stops its *absence* being called a shortfall.

### 8f. Lab bills and pharmacy bills are separate documents

The schema does not assume one file: `PharmacyBill` may carry tests, medicines,
or both, and so may be a lab invoice with no medicines at all.

The engine has to say the same thing, or a prescription reconciled against a
lab-only bill reports every medicine as undispensed. Two symmetric guards:

| Condition | Effect |
| --- | --- |
| bill has lab lines and no medicines | `RX_NOT_BILLED` softens to warning — a lab bill is not evidence a medicine went undispensed |
| bill has medicines and no lab lines | `TEST_NOT_BILLED` softens to warning — a pharmacy bill says nothing about whether a test was performed |

The medicine-side guard records a paired `CHECK_UNAVAILABLE` naming the missing
document; the lab-side one carries its reason on the softened finding itself,
as a stable `softened_code` alongside the prose. Callers branch on the code: a
screen once read "no lab bill supplied" against a bill carrying five lab lines,
because it matched on the sentence and two different reasons shared its shape.
The results screen reads either and states it at the top of the page, above the
verdict — a reviewer must not be shown a clean-looking screen for lines nobody
examined.

---

## 9. The response reports the dictionary match

`ReconciliationResult.canonical` carries, for every medicine line on both
documents, what `normalize.matcher` resolved it to: canonical name, salt
composition, score and method.

The engine computed this on every run from the beginning and had no way to
report it. A salt therefore reached a client only as a **side effect** of a
`BRAND_SUBSTITUTION` or `SCHEDULE_H_UNBACKED` finding happening to fire and
stashing one in its detail. Augmentin, Pan-D, Montair-LC and Zerodol-SP all
resolve perfectly against the dictionary and still showed nothing wherever no
such finding was raised.

It is deliberately a separate object rather than a value written onto
`PrescribedItem.salt`. That field is what the model **read off the page**, and
is usually null because prescriptions print brand names. Conflating a value
transcribed from a document with a value looked up in a dictionary is exactly
what the identity rule exists to prevent.

An unresolved line appears with `method: "unresolved"` and null name and salt,
rather than being omitted — so a caller can tell "looked up, no match" from
"never looked up".

---

## 10. Reimbursement assessment

`ReconciliationResult.reimbursement` sorts every billed line -- medicines and
lab lines alike -- into exactly one of three buckets, each traceable to the
lines that built it.

**No insurance calculation is attempted.** Copay tiers, coverage rules, policy
limits and exclusions appear in none of the documents this system reads.
Modelling them would be the never-guess rule broken at the level of money,
which is the worst place to break it. The words "approved", "claim" and
"settlement" are absent from the copy by design, and a test pins that.

| Bucket | Rule |
| --- | --- |
| `eligible` | paired to a prescribed line with nothing against it |
| `not_eligible` | `BILL_NOT_PRESCRIBED`, `SCHEDULE_H_UNBACKED` or `TEST_NOT_PRESCRIBED` |
| `needs_review` | a check on the line could not run, or the line carries a discrepancy |

Precedence is `not_eligible` > `needs_review` > `eligible`: a line the engine
says was never prescribed does not become "needs review" merely because some
other check on it also failed to run.

Lab lines are included. Excluding them would report zero supported against a
diagnostic bill, which is a lab invoice read as if it were empty.

### Money that cannot be added

A bill line with no printed amount is counted in `lines_without_amount` and
left out of every total, never treated as zero. A total silently missing a line
is worse than one that says it is incomplete.

### What this looks like on real bills

`needs_review` is the largest bucket in practice, and that is not a defect.
Indian pharmacy invoices price per pack and rarely state whether the quantity
column counts packs or units, so `QUANTITY_AMBIGUOUS` attaches to most lines
and the quantity check genuinely did not run. Measured across the bundled
samples, `eligible` is frequently zero for exactly this reason. Treating an
unrunnable check as support would be the same error as reporting a skipped
check as a pass.

---

## 11. Bill integrity

Four checks about the bill as a document, independent of the prescription. All
deterministic Python; no model is in the judgement path.

### 11a. Arithmetic

| Code | Severity | Fires when |
| --- | --- | --- |
| `LINE_TOTAL_MISMATCH` | warning | quantity x rate, less any printed discount, is not the line total |
| `SUBTOTAL_MISMATCH` | warning | the line totals do not sum to the printed subtotal |
| `GRAND_TOTAL_MISMATCH` | warning | subtotal plus tax is not the amount payable |
| `TAX_INCLUSIVE_PRICING` | info | the rates already include tax, so the grand-total check was skipped |

Three false-positive sources are handled rather than tolerated. **Rounding**: a
tolerance of 0.05 per line and 1.00 on totals. **Discounts**: a printed discount
is subtracted; where none is printed but the line is merely *cheaper* than the
arithmetic by up to 30%, nothing is emitted, because an unprinted discount and a
keying error are indistinguishable from the page and only one is worth accusing
a pharmacy of. Being *overcharged* is always reported — a discount explains a
cheaper line, nothing explains a dearer one. **Inclusive GST**: a grand total
equal to the subtotal means tax was not added on top, which is a printing
convention, not an error.

Every check sums medicines and lab lines together. A bill's subtotal covers both
sections, and summing only the medicines manufactured a shortfall on a real
bill.

### 11b. GSTIN

`validate/gstin.py` checks length, the state-code/PAN/entity/Z pattern, the
state code against the 38 statutory codes, and the modulus-36 check digit over
the first 14 characters. The quotient-plus-remainder step is what makes the
digit sensitive to a transposition rather than only a substitution.

**This proves the number is well-formed. It does not prove the business exists
or is registered.** Live verification needs the GST portal API and is out of
scope. Copy says "not a valid GSTIN format" and states that no registry was
consulted; a test pins that the words "not registered", "unregistered" and
"verified" never appear.

`GSTIN_STATE_MISMATCH` is `info` and never more: a chain legitimately bills from
a state it is not addressed in.

### 11c. Drug licence

**No format validation is attempted, deliberately.** There is no national format
and no checksum — 36 state authorities each use their own convention, and
`TN/2019/337821`, `KA-B-21/1234` and `20B/MH/1998/554` are all plausible.
Rejecting a valid licence would put a compliance accusation against a pharmacy
on the basis of a format this system invented, which is worse than not checking.

`LICENCE_ABSENT` (warning) is the only rule: whether a number is printed at all.
`LicenceCheck` deliberately has no `valid` field, so a caller cannot read
presence as validation.

### 11d. Non-medicine lines

`NON_MEDICINE_ITEM` (info) marks cosmetics, supplements, devices and charges.
Classification is three-state — medicine, non-medicine, **unclassified** — and
the dictionary always wins over the keyword list. An unrecognised line is left
unclassified rather than guessed: misclassifying a real medicine as a cosmetic
would silently drop it from reimbursement, which is the worst outcome available.

It is `info`, and it never suppresses or downgrades another finding. In the
reimbursement view it is its own quiet category rather than "not on
prescription" — a delivery charge is out of scope, not an accusation.
`SCHEDULE_H_UNBACKED` and `TEST_NOT_PRESCRIBED` still outrank it.

---

## 12. History checks

Three checks that compare a bill against scans already on record, in
`reconcile/history.py`.

**No database access.** Prior scans arrive as plain `PriorScan` data that the
API has already loaded and already narrowed to what the signed-in account may
see. The engine stays pure, and a history check cannot widen its own
visibility. `reconcile()` takes `priors=None` to skip the checks entirely, or an
empty list to run them against no history — which reports that they could not
run.

| Code | Severity | Fires when |
| --- | --- | --- |
| `DUPLICATE_BILL` | critical | same bill number and pharmacy, with identical lines and totals |
| `POSSIBLE_RESUBMISSION` | warning | the same bill, but something differs |
| `EARLY_REPEAT` | warning | the same salt claimed again inside the earlier course |
| `LICENCE_INCONSISTENT` | warning | one pharmacy carrying different licence numbers |

### 12a. A correction is not fraud

The same pharmacy re-issuing a bill with a fixed line looks, on a bill number,
exactly like someone claiming twice. Where the earlier scan and this one differ
in lines or totals, this reports `POSSIBLE_RESUBMISSION` and names the
differences, rather than an accusation. An honest correction reported as fraud
is the more expensive error.

### 12b. Thin history is not a clean result

A duplicate check against one prior scan proves almost nothing. Below
`MIN_MEANINGFUL_HISTORY` prior scans, an absence of duplicates is reported as a
check that could not run rather than passing silently. A first scan produces
that for all three checks: it is not a clean history, it is no history.

### 12c. Never a repeat of itself

A prior identified as *this* bill is excluded from the repeat check. Without
that, re-running a bill reported every salt on it as "claimed 0 days ago" — a
second accusation about a document the duplicate check had already named. One
finding per salt, against the most recent prior claim of it.

### 12d. Course length is never assumed

`EARLY_REPEAT` depends on `duration_days`, which is null far more often than
not. Where the earlier prescription stated no resolvable duration, the check
reports that it could not run and names why. **No default course is
substituted** — that is the fabricated-duration problem already fixed once in
extraction.

Matching is on the canonical salt, not the brand, or a Dolo-then-Calpol repeat
slips through.

### 12e. Visibility travels with the finding

Every history finding carries a `history_scope` note stating what was searched.
An employee's duplicate check reads only their own scans and says so; it must
not imply the whole record was searched, and must not reveal that another
account's scans exist.

---

## 13. Expiry

| Code | Severity | Fires when |
| --- | --- | --- |
| `EXPIRED_ITEM` | critical | the bill is dated after the line's expiry |
| `EXPIRY_NEAR` | warning | the line expires within 30 days of the bill |

`BilledItem.expiry` holds the **last day a line is valid**. A bill printing
`07/2026` means good through 31 July 2026, so the month resolves to its final
day. Treating it as the 1st would call a medicine expired for most of the month
it was still valid in, and a bill dated ON the expiry day did not dispense an
expired medicine.

`expiry_raw` is transcribed verbatim and resolved in Python, like every other
date. `07/2026`, `JUL 26`, `07-2026` and `2026-07` all resolve; a three-part
date is handed to the ordinary date resolver so its ambiguity rules still
apply, and anything unrecognised is refused rather than guessed.

**Both the expiry and the bill date are required.** An undated bill cannot
clear a medicine of being expired, so a missing either reports a check that
could not run.

## 14. Two failures found together, and what they had in common

### A subtotal error swallowed as a discount

`SUBTOTAL_MISMATCH` treated any subtotal below the line sum, with no discount
printed, as an unitemised discount when the gap was under 30%. That silently
accepted **any subtotal error up to 30% of the bill** — a 190-rupee gap on a
2,158-rupee bill passed unreported.

A bill has a place to print a discount total. Where it did not, the difference
is now reported and the possible explanation is offered in the wording rather
than assumed. The line-level heuristic is unchanged: many bills have no
discount column at all, so a cheaper *line* is still read as a discount.

### Document-level unavailable checks were visible nowhere

The line-level "could not check" reasons live on the reimbursement lines. The
DOCUMENT-level ones — patient name, document date, expiry, subtotal, GSTIN
format — attach to no billed line, so nothing carried them once the old panel
was removed, and they appeared only in the raw JSON.

Three planted defects went unreported at once and two of them traced back to
this. They are surfaced again as a compact expandable line naming each check
and what it needed. **A check that silently goes unavailable is the failure
this project keeps having to fix**, and the lesson is that removing a surface
must be checked against everything that surface was carrying.

