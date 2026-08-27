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

Quantity comparison must also be pack-aware: a bill line reading `2` against a
pack marked `10'S` is twenty units. Use `normalize.units.parse_pack_size`, and
skip when it returns `method="unrecognised"`.

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
