# Null-input audit

Three times a *correct* never-guess null produced wrong engine behaviour, and
each was found by accident. This is the deliberate sweep.

Method: build a prescription/bill pair that reconciles cleanly (`match`, score
100, zero findings), null one field at a time, and record what changes. Probe
script preserved in the commit that added this document.

## Result, before fixes

**23 null cases probed. 16 changed nothing at all** — verdict stayed `match`,
score stayed 100, no finding was emitted. The caller sees a clean pass for checks
that never ran.

| Outcome | Count | Meaning |
| --- | --- | --- |
| (a) correct skip, finding emitted | 2 | `STRENGTH_UNIT_UNSTATED`, `QUANTITY_AMBIGUOUS` |
| **(b) silent skip, no finding** | **16** | a check the caller believes ran, and did not |
| **(c) false positive** | **2 clusters** | absent data asserted as a discrepancy |
| **(d) suppression of a dependent rule** | **2** | one rule's silence disables another |

## (c) False positives — absent data asserted as discrepancy

**An unidentifiable drug name becomes two critical findings.**

| Input | Result |
| --- | --- |
| `rx.drug_name = null` | pairing fails → `RX_NOT_BILLED` **critical** + `BILL_NOT_PRESCRIBED` **critical**, verdict `inconclusive` |
| both drug names unresolvable | same two criticals, verdict **`mismatch`** |

An illegible line cannot pair, so the prescribed item is reported as *not
dispensed* and the billed item as *not prescribed* — two confident accusations
generated from one unreadable line. The truthful statement is "this line could
not be identified, so whether it was dispensed is unknown". The second row is the
worse one: a wholly unreadable pair reports `mismatch`, which reads as a positive
finding of discrepancy.

**`dose_per_administration = null` silently defaults to 1.0.**
`expected_quantity(..., prescribed.dose_per_administration or 1.0)` substitutes a
value the page does not state. With a true dose of 2, `QUANTITY_SHORT` fires
correctly; with the dose nulled, the same bill passes silently. An assumption
inside a rule that exists to catch fabricated numbers.

## (d) Suppression — one rule's silence disables another

| Nulled | Suppressed | Why |
| --- | --- | --- |
| `rx.strength_value` | `BRAND_SUBSTITUTION` | requires `strengths_match`, which is False when unknown |
| `bill.strength_value` | `BRAND_SUBSTITUTION` | same |

This is the sample-03 failure class, still live for a null *value* even after the
null *unit* case was fixed. A legal generic substitution goes unreported whenever
either strength is illegible — and unreported reads as "nothing to see".

Two further dependency failures, found by probing the dictionary path rather than
the field:

| Condition | Suppressed | Why |
| --- | --- | --- |
| billed drug matched by **salt only** | `SCHEDULE_H_UNBACKED` | `entry_for()` needs `source`, which is null for a salt match |
| billed drug **unresolved** | `SCHEDULE_H_UNBACKED`, `DUPLICATE_THERAPY` | both require a resolved dictionary entry |

`SCHEDULE_H_UNBACKED` is the most serious of these. Its entire purpose is
catching prescription-only medicine dispensed with nothing behind it, and it is
silently inert whenever the bill prints a generic name — `Alprazolam` instead of
`Alprax` produces no finding at all.

## (b) Silent skips — the bulk of the problem

Every row below leaves the verdict at `match`, the score at 100, and emits
nothing.

| Nulled field | Check that silently did not run |
| --- | --- |
| `strength_value` (either side) | `STRENGTH_MISMATCH` |
| `form` (either side) | `FORM_MISMATCH` |
| `frequency_raw` | `QUANTITY_SHORT` / `QUANTITY_EXCESS` |
| `duration_raw` **and** `duration_days` | `QUANTITY_SHORT` / `QUANTITY_EXCESS` |
| `dose_per_administration` | quantity, via the 1.0 default above |
| `bill.quantity` | all quantity rules |
| `bill.pack_size` | all quantity rules |
| `bill.unit_price` / `line_total` | pack-basis resolution → quantity rules |
| `patient_name` (either side) | `PATIENT_NAME_MISMATCH` |
| `date_issued` / `bill_date` | `DATE_ANOMALY` |
| `agreement` (N=1) | `LOW_CONFIDENCE_FIELD`, and the agreement route to `inconclusive` |
| `run_item_counts` length 1 (N=1) | `ITEM_COUNT_UNSTABLE` |
| `bill.drug_name` (raw_text still resolves) | nothing — resolution fell back correctly |

`DATE_ANOMALY` deserves particular note: handwritten dates are *designed* to come
back null when ambiguous, so on three of the four real sample photographs this
check has never once run, and nothing in the output says so.

## The shape of the problem

The engine has no concept of "could not check". A rule either fires or is absent,
and absence is rendered identically to "checked, nothing found". Every (b) row is
the same bug repeated: `if x is not None and y is not None: ...` with no `else`.

Confirmed by construction: nulling `frequency_raw` on an otherwise perfect pair
yields `match`, score 100, zero findings — indistinguishable from a bill verified
correct down to the tablet.

## Fixes applied

1. **`CHECK_UNAVAILABLE`** (info) — a first-class finding emitted whenever a
   named check cannot run, carrying the check name, the missing inputs and which
   document they were missing from. Every (b) row above now produces one.
2. **`ReviewSummary.checks_unavailable`** — a count of those, surfaced in the UI
   beside `items_needing_review`, so "we checked and found nothing" and "we could
   not check" never render identically.
3. **`RX_NOT_BILLED` / `BILL_NOT_PRESCRIBED` severity depends on identification.**
   An unidentifiable line drops to `warning` with a message saying the absence
   could not be confirmed. Only an identified drug supports a critical claim.
4. **`BRAND_SUBSTITUTION` no longer requires known strengths.** It fires when the
   salts match and the brands differ, provided no strength mismatch was found,
   and records whether the strength could be verified.
5. **`SCHEDULE_H_UNBACKED` resolves through the salt** when no brand matched, so
   generic-name billing is no longer invisible.
6. **`dose_per_administration = null` no longer defaults to 1.0.** The quantity
   check reports itself unavailable instead of assuming a dose the page does not
   state.

7. **Positional frequency notation is exempt from the dose requirement**, and
   this is not an assumption. In `1 - 0 - 1` the slots state the units taken at
   each time of day and `doses_per_day` already sums them to units per day, so
   requiring a separate `dose_per_administration` would both block the check and
   double-count when one was supplied. A Latin code such as `BD` carries no such
   information, so a missing dose there genuinely blocks the calculation. Removing
   the blanket 1.0 default without this distinction made quantity unavailable on
   every real line.

Held by `api/tests/test_null_matrix.py`, including hypothesis property tests that
null arbitrary subsets of fields and assert the engine never raises, never emits a
critical from absent data alone, never skips a check without saying so, and keeps
`checks_unavailable` equal to the findings it summarises.

## Result, after fixes

Re-probing the same 23 cases:

| Outcome | Before | After |
| --- | --- | --- |
| (a) correct skip, finding emitted | 2 | **21** |
| (b) silent skip | **16** | **2** |
| (c) false positive | 2 clusters | **0** |
| (d) suppression | 2 | **0** |

The two remaining silent cases are correct: nulling `bill.drug_name` still
resolves through `raw_text`, and the price fields are not needed when
`units_basis` is declared. Neither is a check that failed to run.

The property test also found a case the hand-written probe missed. An identified
prescription line paired with an *unidentifiable billed line* still raised a
critical `RX_NOT_BILLED` — but that unreadable line may be the counterpart, so the
absence could not be confirmed. Both counterpart rules now require that the other
document has no unidentified unmatched lines before they will claim critical.

### Effect on the corpus

Verdicts and scores are unchanged across all four samples — `p3-dental` 17,
`sample-01` 100, `sample-02` 25, `sample-03` 92 — so nothing regressed. What
changed is what the caller can see:

| Sample | Checks that could not run |
| --- | --- |
| `p3-dental` | 2 — a strength, and the document date |
| `sample-01` | 1 — the document date |
| `sample-02` | 1 — the document date |
| `sample-03` | 1 — the document date |

Every one of those was previously invisible. `sample-01` in particular still
reports `match` at score 100, and now says alongside it that one check never
ran — which is the distinction the whole audit exists to preserve.

---

# Lab test fields

Added with the lab-test engine and audited **as it was built**, not after. Same
method: a baseline that reconciles cleanly (LFT ordered, seven analytes billed,
`match`, score 100, zero test findings), then one field nulled at a time.

**13 null cases probed.**

| Outcome | Count | Fields |
| --- | --- | --- |
| (a) correct skip, finding emitted | 5 | `test_name` (both sides), `investigations_present` = true / null, unresolvable panel |
| (b) silent skip, no finding | **0** | — |
| no check depends on the field | 7 | `panel`, `urgency`, `unit_price`, `line_total` |
| (c) false positive | **0** | — |
| (d) suppression | **0** | — |

`panel` and `urgency` are carried for display and for a reviewer's context; no
rule consumes them, so nulling them skips nothing. That is a different statement
from "the check silently passed", and the distinction is the point of this table.

## What each null does

| Nulled | Result | Why this is right |
| --- | --- | --- |
| `rx test.test_name` | falls back to `raw_text`; if that will not resolve → `TEST_UNRESOLVED` info + `CHECK_UNAVAILABLE`, and every billed line softens to **warning** | an unidentified order is not evidence that nothing was ordered |
| `bill test.test_name` | same on the bill side; the ordered panel softens from critical to **warning** | an unidentified bill line may well be the ordered test |
| `bill test.quantity` | `CHECK_UNAVAILABLE` (repeat test billing), once per document | **found by this audit.** Was a silent skip: a test billed with quantity 2 passed unchallenged. Now `TEST_DUPLICATE` fires on quantity > 1, and a null quantity says the check could not run rather than implying it passed |
| `rx.investigations_present` = null, no tests read | accusations soften to **warning** + `CHECK_UNAVAILABLE` | the section's absence was never confirmed |
| panel not in `lab_panels.py` | `TEST_UNRESOLVED` info, components stay empty, **no** `TEST_NOT_BILLED`, billed lines soften to warning | see below |

## The two designed-against cases

Both are the same failure — one unreadable line becoming several confident
accusations — and both were built against rather than discovered.

**Present but unreadable is not absent.** `tests = []` with
`investigations_present = true` means the section exists and could not be read.
Treating that as "no tests ordered" would make every billed test unauthorised.
Only `investigations_present = false` — a positive observation that the page has
no such section — licenses a critical.

| `investigations_present` | tests read | one test billed |
| --- | --- | --- |
| `true` | none | **warning** + `CHECK_UNAVAILABLE`, verdict `match_with_warnings` |
| `false` | none | **critical**, verdict `mismatch` |
| `null` | none | **warning** + `CHECK_UNAVAILABLE` |

**An unresolved panel expands to nothing, not to zero components.** An
unresolved `LabMatch` carries an empty component tuple, and an empty set
trivially covers nothing — so the naive reading reports every billed component
as unprescribed. One illegible word, four criticals. The resolution is checked
before the components are used: an unresolved order is reported as unresolved
and is never compared, and its presence softens every billed-line accusation.

`test_an_unresolvable_panel_does_not_accuse_every_billed_component` in
`api/tests/test_lab.py` pins this: four billed LFT analytes against one
unreadable order line yield four **warnings**, zero criticals, and a stated
reason on each.

## Property coverage

`api/tests/test_null_matrix.py` generates every subset of the nullable test
fields alongside the medicine fields, and the existing four properties now hold
across both — in particular *absent data alone never produces a critical* and
*a skipped check is always recorded*.

---

## Panel/component parsing (lab_panels)

Added when billed lines printed as ``Lipid Profile — Total Cholesterol`` and
``Thyroid Profile (T3, T4, TSH)`` failed to resolve while the bare names on the
prescription resolved fine. Audited the same way: one input at a time, and what
changes.

**11 degenerate inputs probed. 0 raised, 0 resolved to anything.**

| Input | Result | Why this is right |
| --- | --- | --- |
| `None`, `""`, `"   "` | unresolved | nothing to look up |
| `"—"`, `" - "`, `":"`, `"\|"`, `"— —"` | unresolved | a separator with nothing either side of it names no test |
| `"()"` | unresolved | an empty parenthetical strips to nothing |
| `"(T3)"` | `T3` | the parenthetical IS the name here, and it resolves exactly |
| 300 characters | unresolved | far below the fuzzy threshold, as it should be |
| `"Lipid Profile — Zzz Unknown"` | **unresolved** | a panel with an unreadable component is not the whole panel |

That last row is the one that matters. Resolving a half-readable panel line to
the panel would let one line satisfy an order of six, which is the panel
decomposition feature firing in reverse. The threshold was never lowered: what
was added is parsing of how laboratories print a line, not tolerance for a weak
match.

| Nulled | Result |
| --- | --- |
| `rx test.test_name` with a resolvable billed line | `TEST_UNRESOLVED` + 3 `CHECK_UNAVAILABLE`, **0 criticals** |
| `bill test.test_name` with a resolvable order | softened `TEST_NOT_BILLED` + 2 `CHECK_UNAVAILABLE`, **0 criticals** |
| both nulled | 4 `CHECK_UNAVAILABLE`, **0 criticals**, verdict `match` |

`softened_code` is present on every softened `TEST_NOT_BILLED` and
`TEST_NOT_PRESCRIBED`, `None` when the finding is confident, and never absent.
`required_components()` on an unknown panel returns `()` rather than raising.

