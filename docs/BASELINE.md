# Extraction baseline — PROVISIONAL

| | |
| --- | --- |
| Date measured | **2026-08-27** |
| Model | `gemini-3.7-flash` |
| Prompt version | `2026-08-27.1` |
| Location | `global` |
| Temperature | **0.3** (raised from the production 0.0 solely to probe run-to-run stability) |
| Corpus | `samples/p1..p4.jpg` — 4 real photographs |
| Runs | 3 per photo, 12 extractions, `--no-cache` throughout |

## What this document does and does not establish

**Verified here:** self-consistency and calibration. Whether the model returns
the same answer three times, and whether its `confidence` score predicts that.

**NOT verified here:** accuracy. Ground truth for the drug names is still
unconfirmed. No claim in this document says any extracted value is *correct* —
only whether it is *reproducible*. A field can be stable across all three runs
and still be wrong.

**Corpus caveat:** p1 and p2 are Bangladeshi prescriptions written partly in
Bengali; p3 and p4 are Indian. The extraction prompt frames the task as an
*Indian* prescription. This mismatch is known and deliberately left in place.

---

## (a) Per-item field agreement across 3 runs

Agreement is over `drug_name`, `strength_value`, `strength_unit`, `duration_days`.

| Photo | Items/run | Fields at 3/3 | Notes |
| --- | --- | --- | --- |
| p1 | 4, 4, 4 | 15/16 | `rx-01 duration_days` disagreed: **14 / 7 / 7** |
| p2 | 4, 4, 4 | 15/16 | `rx-02 strength_unit` disagreed: **IU / null / null** |
| p3 | 4, 4, 4 | 15/16 | `rx-03 drug_name`: `Pan-D` / `Pan-D` / `Pan D` (cosmetic) |
| p4 | **7, 7, 6** | see below | item count itself unstable |

p3 is the cleanest document (printed English letterhead, clear hand) and is
effectively stable. p1 and p2 each have exactly one unstable field. p4 is not
stable at all.

### p4, re-aligned by content

Index alignment is misleading for p4 because run 3 omits a line and shifts every
subsequent index. Aligned by line content instead:

| Line | Present | `drug_name` agreement | Detail |
| --- | --- | --- | --- |
| `- 6# (P+H)` | **2/3** | — | **line vanished entirely in run 3** |
| `Inj Xgeva - alt # (120)` | 3/3 | 3/3 | stable |
| `HMW - TDS` | 3/3 | 2/3 | `HMW` / null / null |
| `Clogen - TDS` | 3/3 | **1/3** | `Clogen` / null / `Clocen` — all three differ |
| `T. Dexa (4) - BD D2-D4` | 3/3 | 3/3 | stable |
| `T. Ondam (4) - BD D2-D4` | 3/3 | 2/3 | `Ondam` / `Ondam` / `Ondan` |
| `T. Ultracet - BD x 5 days` | 3/3 | 3/3 | stable |

A whole prescribed line disappearing on 1 of 3 runs is the most serious result
in this table. Reconciliation cannot flag a discrepancy on a line it never saw.

---

## (b) Confidence reported on fields that did not reproduce

This is the load-bearing section.

| Photo | Item | Field | run 1 | run 2 | run 3 | confidence |
| --- | --- | --- | --- | --- | --- | --- |
| p1 | rx-01 | `duration_days` | 14 | 7 | 7 | 0.85 / 0.85 / 0.85 |
| p2 | rx-02 | `strength_unit` | `IU` | null | null | 0.85 / 0.85 / 0.80 |
| p3 | rx-03 | `drug_name` | `Pan-D` | `Pan-D` | `Pan D` | 0.95 / 0.95 / 0.95 |
| p4 | — | `6# (P+H)` line | present | present | **absent** | 0.85 / 0.85 / — |
| p4 | — | `HMW` | `HMW` | null | null | 0.85 / 0.80 / 0.85 |
| p4 | — | `Clogen` | `Clogen` | null | `Clocen` | 0.80 / 0.75 / 0.82 |
| p4 | — | `Ondam` | `Ondam` | `Ondam` | `Ondan` | 0.88 / 0.90 / 0.92 |

**Highest confidence observed on a field the model could not reproduce: 0.95.**

### Confidence distribution, all 56 item observations

| Statistic | Value |
| --- | --- |
| min | **0.75** |
| max | 0.95 |
| mean | 0.880 |
| median | 0.88 |
| observations below **0.6** (`LOW_CONFIDENCE_FIELD` threshold) | **0** |
| observations below 0.75 | **0** |
| observations at or above 0.8 | 54 of 56 (96%) |

`overall_legibility`, 12 observations: min **0.75**, max 0.95, **0 below 0.4**
(the inconclusive floor).

The scores occupy a 0.2-wide band at the top of the scale. They do not
discriminate between fields that reproduce and fields that do not: `Clogen`,
which produced three different answers in three runs, scored 0.75–0.82, while
`Ondam→Ondan` scored 0.88–0.92.

---

## (c) Bengali transcription check — p1, p2

Are the Bengali `raw_text` values byte-identical across the three runs?

| Item | Result |
| --- | --- |
| p1 rx-01 | **BENGALI DIFFERS** |
| p1 rx-02, rx-03 | Bengali identical; Latin portion differs (whitespace/newlines) |
| p1 rx-04 | exact match |
| p2 rx-01 | **BENGALI DIFFERS** |
| p2 rx-02 | Bengali identical; Latin differs |
| p2 rx-03 | exact match |
| p2 rx-04 | **BENGALI DIFFERS** |

### The diffs that matter

**p1 rx-01** — the duration changes meaning between runs:

```
run 1: 'Cap. Erdon TR 100mg\n১+০+০ খাওয়ার পর - ২ সপ্তাহ'   ("2 weeks")
run 2: 'Cap. Erdon TR 100mg ১+০+০ খাওয়ার পর - ১ সপ্তাহ'    ("1 week")
run 3: 'Cap. Erdon TR 100mg ১+০+০ খাওয়ার পর - ১ সপ্তাহ'    ("1 week")
```

The earlier baseline run of the same photo produced `৭ দিন` ("7 days") for this
line. That is **three different readings of one Bengali duration**, every one
delivered at confidence 0.85, and it is consistent with `duration_days` flipping
14 / 7 / 7 in section (a).

**p2 rx-01** — a different Bengali word each run: `চলে` / `চলবে` / and a run
that rendered the dose as Latin `2 1/2` rather than `2½`.

**Interpretation.** `raw_text` is specified as a verbatim transcription. A
verbatim transcription of a fixed image is a deterministic function of that
image; it should not change with sampling temperature. Bengali that varies in
*content* — not just whitespace — across independent runs is evidence of
generation rather than transcription. This is undetectable by anyone reading the
output who does not read Bengali, which is precisely what makes it dangerous.

Note the failure is not uniform: several Bengali strings *are* stable. The model
appears to transcribe legible Bengali and generate plausible Bengali where the
script is unclear, with no signal distinguishing the two.

---

## (d) Verdict

> **No. These confidence scores are not usable as a threshold signal.**

Every observation fell between 0.75 and 0.95. Nothing scored below 0.6, so
`LOW_CONFIDENCE_FIELD` (0.6) would never have fired on this corpus. No
`overall_legibility` fell below 0.4, so the inconclusive floor would never have
fired either. Both thresholds are, on this evidence, decorative.

Worse than being uninformative, the scores are *anti*-correlated with the thing
they should track in at least one case: the most reproducible document (p3,
printed English) and an unreproducible field on p4 both scored 0.95.

The two worlds the measurement was designed to separate — "handwriting is
legible" versus "the model reports high confidence regardless" — resolve toward
the second. p4 could not reproduce its own item count across three runs while
reporting a mean confidence of 0.88.

**Consequence:** confidence must not gate anything user-visible until it is
recalibrated. Self-consistency across N runs is a working substitute — it
detected every instability in this document, and it needs no cooperation from
the model.

---

# After fixes — 2026-08-27

Three never-guess fixes applied after the measurement above. Prompt version
bumped `2026-08-27.1` → **`2026-08-27.2`**, which invalidates the cache. Model
and temperature unchanged (`gemini-3.7-flash`, production temperature 0.0). Each
photo re-run once with `--no-cache`.

**Thresholds were not touched.** The `LOW_CONFIDENCE_FIELD` (0.6) and
inconclusive-legibility (0.4) values are unchanged, and section (d) above still
stands: they remain unfired by anything in this corpus.

## Fix 1 — do not infer a strength unit that is not written

| Item | Before | After |
| --- | --- | --- |
| p2 `THYROX 50` | `null` (already correct) | `null` |
| p2 `DEFROL 40000` | **`IU`** (invented) | **`null`** |
| p2 `ESIPRAM 5` | **`mg`** (invented) | **`null`** |
| p2 `SENTIX 0'5` | `mg` | **`null`** |

All four now return `strength_value` with `strength_unit: null`, because no unit
is written next to any of those numbers. Fixed.

## Fix 2 — `duration_raw` added; no conversion during extraction

`PrescribedItem.duration_raw` and `PrescribedItemDTO.duration_raw` added.

| Item | `duration_raw` | `duration_days` | Correct? |
| --- | --- | --- | --- |
| p2 `ESIPRAM` | `৬ মাস` (6 months) | **`null`** | yes — was **180** |
| p2 `SENTIX` | `২ মাস` (2 months) | **`null`** | yes — was **60** |
| p2 `THYROX` | `৪৫ দিন` (45 days) | `45` | yes — plain days, no assumption |
| p1 `Sergel`, `Mydocalm`, `Bost` | `১ মাস` | `null` | yes |
| p1 `Erdon TR` | `৭ সপ্তাহ` (weeks) | `null` | yes — weeks are not days |
| p3 `Augmentin`, `Enzoflam`, `Pan-D` | `x 5days` | `5` | yes |
| p3 `Hexigel` | `x 1week` | **`null`** | yes — was **7** |

The month→day multiplications are gone. Note `৪৫ দিন` → 45 still populates,
correctly: the page states days, so no assumption is required. Fixed.

## Fix 3 — rank date sources: printed labelled field > header > body text

Applied, and working — **but it does not change p4's output, and the premise
recorded in the previous session was wrong.**

Zooming into the p4 form field shows `Appointment Date : 1?-02-2021`, where the
second digit is physically obscured by handwritten ink struck over it. It is not
legibly `19`. The earlier claim that "a printed `19-02-2021` sat unused and would
have resolved cleanly" was a misreading of the full-page image.

Evidence the fix is nevertheless working — the returned `date_issued_raw` format
now matches the printed field rather than the body text:

| Source on page | Format | Model returned |
| --- | --- | --- |
| Advice text | `10/2/21` — slashes, 2-digit year | — |
| Printed labelled field | `1?-02-2021` — dashes, 4-digit year | **`10-02-2021`** ✅ |

So source selection is now correct. `date_issued` remains `null` with an
ambiguity warning, which is the right outcome twice over: the digit is obscured,
and `10-02` is day/month ambiguous regardless. **p4 has no resolvable issue date.
No fix can produce one, and inventing one is exactly what must not happen.**

## Not addressed

- Confidence calibration. Section (d) stands: still unusable as a threshold.
- Item-count instability on p4 (7/7/6) and the dropped `6# (P+H)` line.
- Bengali transcription instability from section (c) — `duration_raw` now
  preserves the Bengali verbatim, which makes the instability *visible* in the
  output, but does not make the transcription stable.
- Accuracy. Ground truth for drug names is still unconfirmed.
