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

### 8e. An unresolved panel expands to nothing, not to zero components

An unresolved `LabMatch` carries an empty component tuple, and an empty set
trivially covers nothing — so a naive reading reports every billed component as
unprescribed. One illegible word, four criticals: the same shape as the
unidentifiable-drug false positive fixed earlier.

Resolution is therefore checked before components are used. An unresolved order
is reported as `TEST_UNRESOLVED` plus `CHECK_UNAVAILABLE`, is never compared,
never produces `TEST_NOT_BILLED`, and its presence softens every billed-line
accusation to a warning carrying a stated reason.

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
document; the lab-side one carries its reason on the softened finding itself.
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

