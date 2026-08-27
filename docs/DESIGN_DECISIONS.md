# Design decisions

Decisions that came out of measurement rather than assumption. Each records what
was believed, what the evidence showed, and what changed as a result.

Evidence lives in [BASELINE.md](./BASELINE.md): 12 extractions, 3 runs each over
4 real photographs, `gemini-3.7-flash`, prompt version `2026-08-27.1`, measured
**2026-08-27**.

**Status: recorded, not implemented.** Nothing in this document is built yet.
It exists so the reconciliation engine is designed against evidence.

---

## 1. Model confidence is not a gating signal

**Originally assumed.** That `confidence` and `overall_legibility` tracked
legibility, so a `LOW_CONFIDENCE_FIELD` threshold at **0.6** and an inconclusive
legibility floor at **0.4** would separate readable fields from unreadable ones.

**Measured.** Across 56 item observations:

| Statistic | Value |
| --- | --- |
| range | **0.75 – 0.95** |
| mean / median | 0.880 / 0.88 |
| below 0.6 (`LOW_CONFIDENCE_FIELD`) | **0** |
| below 0.75 | **0** |
| at or above 0.8 | 54 of 56 (96%) |

`overall_legibility`, 12 observations: range 0.75–0.95, **0 below 0.4**.

Both thresholds would have fired **zero times** on this corpus, including on the
document the model could not reproduce at all.

The scores are not merely compressed — they invert against reproducibility in
places. `Clogen` returned three different values in three runs (`Clogen` / null /
`Clocen`) at 0.75–0.82, while stable fields on the cleanest document scored 0.95.
The highest confidence observed on a field the model could not reproduce was
**0.95**.

**Decision.** **Raw model confidence gates nothing user-visible.** It may be
retained and displayed as a model-reported number, clearly labelled as such, but
no rule code, verdict, or threshold may read it. The 0.6 and 0.4 values are
withdrawn as specified; any future threshold must be set against a recalibrated
signal and re-measured.

---

## 2. Self-consistency is the confidence input

**Decision.** Extraction runs **N = 3 at temperature 0.3** by default, and the
**per-field agreement ratio across those runs** becomes the confidence input that
section 1 removed.

| Agreement | Ratio | Reading |
| --- | --- | --- |
| 3/3 | 1.00 | reproducible |
| 2/3 | 0.67 | majority, with a dissent |
| 1/3 | 0.33 | three different answers — no reproducible value exists |

**This is core pipeline behaviour, not an enhancement and not optional.** Three
Flash-tier calls per document is a negligible cost next to reporting a clean
match on a hallucinated drug name.

**Why temperature 0.3 and not 0.0.** Temperature 0.0 suppresses the variance this
signal depends on. Near-deterministic sampling would return three near-identical
answers and manufacture the appearance of stability — the same false reassurance
the raw confidence score already provides. The variance is the measurement, so it
must not be tuned away. Production extraction previously ran at 0.0; adopting
N=3 means adopting 0.3 with it.

**What this does not fix.** Self-consistency detects instability; it does not
detect a *stable* wrong answer. A model confidently misreading the same drug name
three times scores 3/3. This measures reproducibility, never accuracy.

---

## 3. Item-count instability needs its own finding

**Observed.** p4 returned **7, 7 and 6 items** across three runs. The line
`- 6# (P+H)` was present twice and absent once.

**Why this is critical rather than a warning.** Every other failure mode surfaces
as a discrepancy a human can see. This one is silent. If a prescribed line is
missing from the extraction, the reconciliation engine has nothing to match, so
it raises no `RX_NOT_BILLED` — it reports a **clean match on a line it never
saw**. A false negative that presents as a pass is the worst possible output from
a tool whose purpose is catching omissions.

**Decision.** Reserve rule code **`ITEM_COUNT_UNSTABLE`**, severity
**`critical`**. Raised when the item count differs across the N runs of a
document.

**Schema interaction to resolve before implementing.** `Finding.prescribed_ref`
must name an existing `PrescribedItem.item_id` — the `ReconciliationResult`
referential-integrity validator enforces this. But an item present in only some
runs may not exist in the canonical extraction, leaving no `item_id` to point at.
`ITEM_COUNT_UNSTABLE` is therefore a **document-level finding with both refs
null**, carrying the evidence in `detail` (per-run counts, and the text of lines
that appeared in some runs only). Confirm this satisfies the validator before
building it.

---

## 4. Non-Latin script handling is unresolved

**Observed.** Bengali `raw_text` is not byte-stable across runs, and the diffs
change meaning rather than formatting:

```
p1 rx-01   run 1: ২ সপ্তাহ   (2 weeks)
           run 2: ১ সপ্তাহ   (1 week)
           run 3: ১ সপ্তাহ   (1 week)
  earlier baseline: ৭ দিন    (7 days)
```

Three readings of one duration, each reported at confidence 0.85.

A verbatim transcription of a fixed image is a deterministic function of that
image. Content that changes with sampling temperature is being **generated, not
transcribed**.

The failure is not uniform — several Bengali strings are perfectly stable. The
model appears to transcribe legible Bengali and generate plausible Bengali where
the script is unclear, **with no signal distinguishing the two**. Latin-script
fields on the same documents were markedly more stable.

**Why this is more dangerous than an ordinary misread.** It is invisible to any
reviewer who does not read the script. An English misreading gets caught by the
person checking the output; a fabricated Bengali duration does not.

**Decision.** Recorded as a **known limitation** and surfaced in the README.
Self-consistency (section 2) detects it, which is currently the only defence.
Not otherwise resolved.

**Corpus note.** p1 and p2 are Bangladeshi prescriptions partly in Bengali, while
the extraction prompt frames the task as an *Indian* prescription. That mismatch
is known and deliberately left in place; it is a candidate explanation for the
instability but has not been tested as one.

---

## What remains unestablished

- **Accuracy.** Ground truth for the drug names is still unconfirmed. Everything
  above concerns reproducibility only. A field can be stable and wrong.
- **Whether a recalibrated confidence signal is achievable** from this model at
  all, or whether agreement ratio is the permanent substitute.
- **Majority-vote resolution rules** for 2/3 and 1/3 fields: which value is
  canonical, and whether 1/3 should collapse to null. Section 2 defines the
  input, not the policy.
