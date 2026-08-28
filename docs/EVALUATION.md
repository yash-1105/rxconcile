# Evaluation

## This POC has no accuracy claim

**Ground truth for drug names on the real corpus was never confirmed.** Nobody
with the source documents in hand has said which extracted values are correct.

Everything measured so far is **reproducibility**: whether the model agrees with
itself across repeated runs. That is a genuinely useful signal — it caught
instability the model's own confidence score completely missed — but it is not
accuracy. A field can be perfectly reproducible and perfectly wrong. Three
identical misreadings score agreement 1.0 and pass every check in this system.

So the honest summary of what has been demonstrated is:

- The pipeline runs end to end on real photographs.
- Extraction is *self-consistent* on clear documents and measurably unstable on
  poor ones.
- The rules fire correctly on documents whose contents are known, because they
  were constructed.
- **Whether the system reads real prescriptions correctly is unmeasured.**

This is the single biggest gap in the work, and the first thing any reviewer will
ask about. The sections below describe what closing it actually takes.

## What a real evaluation requires

### 1. Assemble a corpus

Target **N ≥ 100 real prescription/bill pairs**, and treat that as a floor rather
than a goal. Four photographs cannot separate a systematic failure from a bad
day. Sample deliberately across the axes that plausibly change behaviour:

| Axis | Why it matters |
| --- | --- |
| Handwriting legibility | The dominant difficulty; sample the bad end deliberately |
| Specialty | Oncology shorthand (`6# (P+H)`) differs from a dental script |
| Script | English-only vs mixed Devanagari/Bengali — currently out of scope |
| Bill format | Per-pack vs per-unit rates decides whether quantity is checkable at all |
| Photo quality | Angle, shadow, crop, resolution, phone camera vs scan |
| Pharmacy | Column headings and pack notation vary by chain |

Record provenance and consent for every pair. These are medical records.

### 2. Hand-label ground truth

Label **before** looking at any model output, from the source image alone.
Otherwise the labels drift toward what the model said, and the evaluation
measures agreement with itself again.

For every prescribed and billed line, record each field the schema carries —
`drug_name`, `salt`, `strength_value`, `strength_unit`, `form`, `frequency_raw`,
`duration_raw`, `quantity`, `pack_size`, `units_basis` — and, critically, mark a
field **illegible** where it genuinely is. That third category is not optional:
without it, a correct `null` is scored as a miss and the never-guess rule looks
like a failure.

Have **two independent labellers** cover at least 20% of the corpus and report
inter-rater agreement. If humans cannot agree on what a script says, that ceiling
belongs in the report — it bounds any accuracy figure the system can be given.

### 3. Report extraction accuracy per field

Per field, not aggregated. An aggregate hides the only distinction that matters
here.

| Metric | Definition |
| --- | --- |
| Correct | Extracted value equals the label |
| **Wrong** | Extracted a value, label differs — **the failure that matters** |
| Missed | Extracted `null`, label has a legible value |
| Correctly null | Extracted `null`, label says illegible |

Report **wrong** separately from **missed** and never merge them. A null is a
safe outcome a reviewer can act on; a confident wrong drug name is the failure
this entire system is built to prevent. Collapsing them into "accuracy" destroys
the distinction the design rests on.

Then cross-tabulate wrong-vs-agreement:

- wrong answers at agreement 1.0 — the dangerous quadrant, invisible to every
  check currently implemented
- wrong answers at agreement < 1.0 — caught by the existing signal
- correct answers at agreement < 1.0 — noise cost of the review burden

Only that table can say whether the agreement threshold is set anywhere near
right. It has never been produced.

### 4. Report per-rule precision and recall

Separately for every rule code, against labelled discrepancies — never as one
number.

```
precision = fired correctly / fired at all
recall    = fired correctly / discrepancies actually present
```

The rules have very different costs when wrong, so they need separate targets:

- `RX_NOT_BILLED` and `STRENGTH_MISMATCH` — **recall matters most.** A missed
  discrepancy is invisible; a false one is merely annoying.
- `BILL_NOT_PRESCRIBED` and `SCHEDULE_H_UNBACKED` — **precision matters most.**
  These accuse a pharmacy of dispensing something unprescribed.
- `ITEM_COUNT_UNSTABLE` — recall against deliberately degraded photographs, since
  the failure it guards produces a clean-looking pass.
- `QUANTITY_*` — report the **skip rate** alongside precision and recall. A rule
  that abstains on most real bills has a recall figure that means very little
  without knowing how often it declined to run.

Report the pairing step separately too: pair precision/recall against labelled
correspondences, and the rate at which one prescribed line claims the wrong
billed line. A mispairing quietly changes which rules can fire.

### 5. Regressions

Keep the labelled corpus and re-run it on every prompt change. Extraction is a
prompt away from moving, and `PROMPT_VERSION` invalidates the cache precisely so
this stays honest.

## Known measurement traps

- **Do not evaluate on synthetic samples.** `sample-0*` renders glyphs cleanly.
  Its only job is regression coverage; accuracy on it means nothing.
- **Do not reuse the four development photographs.** They shaped the prompts, the
  drug dictionary and the dash-folding fix. They are training data now.
- **Do not evaluate at N=1** and then ship N=3, or vice versa. The consensus step
  changes what reaches the rules.
- **Do not report a single accuracy number.** Per field, per rule, wrong split
  from missed. Anything coarser hides the distinction the design depends on.
- **Watch for the labeller reading the model's output first.** It is the easiest
  way to produce an impressive and worthless number.

---

## Lab test reconciliation — measured on the real photographs

Fresh extraction (prompt version `2026-08-28.1`, N=3, temperature 0.3) against
the four genuine handwritten prescriptions.

| Sample | `investigations_present` | Tests read | Agreement on the name |
| --- | --- | --- | --- |
| `p1` | true | 1 — `MRI Lumbar spine c screening whole spine` | 0.67 |
| `p2` | true | 2 — both names **null** | 0.50, 0.33 |
| `p3` | **false** | 0 | — |
| `p4` | true | 1 — `CBC` | **1.00** |
| `sample-01/02/03` | false | 0 | — |

Four things in that table are the point of the feature:

**`p3` reports `false`, not zero.** The dental prescription genuinely has no
investigations section. It produces no test findings, no `CHECK_UNAVAILABLE`,
and no score penalty — absence is not a discrepancy.

**`p2` reports two tests with null names.** The line reads
`FT4, TSH - ১½ মাস পর`, part Bengali, and agreement across runs was 0.33. The
extractor left the name null rather than guessing and the engine treats the
orders as unidentified, softening every accusation. This is guard case (e) on
real data, not a constructed fixture.

**`p1` orders imaging, not a lab panel.** `MRI Lumbar spine` resolves against
nothing in `lab_panels.py`, so it is reported `TEST_UNRESOLVED` (info) with a
`CHECK_UNAVAILABLE`. It is not silently dropped and not accused.

**The three existing synthetic pairs still read zero tests** and their results
are unchanged, so medicine-only reconciliation did not regress.

### End to end: p4 against a lab bill

`samples/sample-lab-bill.png` is a diagnostic-laboratory invoice — no batch
numbers, no HSN, no pack sizes — paired with the real `p4`, whose handwriting
orders `① CBC`. The bill itemises a Complete Blood Count the way a real lab
does, plus one Lipid Profile nobody ordered.

| | Result |
| --- | --- |
| Ordered | `CBC` → Complete Blood Count |
| Billed | Haemoglobin, Total WBC Count, RBC Count, Platelet Count, Packed Cell Volume, Differential Count, Lipid Profile |
| Test pairs | **1** |
| Test findings | **one** — `TEST_NOT_PRESCRIBED` (critical) for the Lipid Profile |

**Six billed lines matched one handwritten order and produced no findings.**
Without panel decomposition this document would have reported one unperformed
test and seven unordered ones. That is the whole case for the feature, measured
on genuine handwriting rather than on a fixture.

The six prescribed medicines on `p4` do not appear on a lab bill, and are
reported as **warnings**, each saying the bill carries only lab tests and that
the pharmacy bill is a separate document that may not have been supplied. Before
the separate-documents guard those were six criticals.

The overall verdict is `inconclusive`, correctly: `p4` returned item counts
`[7, 6, 6]` across the three runs, so `ITEM_COUNT_UNSTABLE` fires on the
prescription. The test findings are still computed and shown, as provisional
observations rather than assertions.

### What this does not evidence

The panel dictionary is hand-compiled and unverified, and every panel in it was
chosen by me rather than taken from a laboratory's test master. Decomposition is
only as correct as that table. Only one real prescription in the set (`p4`)
orders a panel this build recognises, so panel matching is evidenced by one
genuine handwriting sample and a synthetic bill — not by a corpus.

