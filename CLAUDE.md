# CLAUDE.md — rxconcile

## Purpose

rxconcile OCRs a handwritten doctor's prescription and a pharmacy bill, and extracts structured data from both.
It then reports whether the medicines dispensed match what was prescribed.
It is a proof of concept, not a medical device.

---

## HARD RULES

These are not style preferences. Violating one is a defect.

### 1. The LLM performs EXTRACTION ONLY
The model's only job is to turn pixels into structured data. **All match/mismatch verdicts come from
deterministic Python in `api/rxconcile/reconcile/`.** Never let the model decide whether two documents
agree. Do not ask the model "do these match?", do not ask it to summarize discrepancies, and do not
pass both documents to it for comparison. Extraction and adjudication are separate stages, always.

### 2. Every finding carries a machine-readable rule code and a severity
No bare strings. A finding is `(rule_code, severity, ...evidence)`. Rule codes are stable identifiers
that a caller can branch on; severity is a fixed enum. Human-readable text may accompany a finding but
never replaces the code.

### 3. Never invent a drug name, strength or quantity that is not legible in the source image
Unreadable fields are emitted as `null` with a confidence score, **never guessed**. Do not infer a drug
name from context, do not autocorrect to the nearest real drug, do not complete a partial word.
**A null is correct; a hallucinated drug name is a critical failure.**

### 4. No medical advice, no dosing recommendations, no clinical judgement in any output
This tool reports document discrepancies only. It never says whether a dose is safe, appropriate,
excessive, or contraindicated. It never suggests what should have been prescribed. Output describes
what the two documents say and where they differ — nothing more.

### 5. Gemini only
Do not add Cloud Vision or Document AI. This build uses Gemini multimodal exclusively and passes image
bytes inline. Those APIs are deliberately not enabled on the GCP project.

### 6. No model ID is ever hardcoded in Python
All three runtime models — `GEMINI_MODEL`, `GEMINI_MODEL_FALLBACK`, `GEMINI_MODEL_QUOTA_FALLBACK` —
come from config only. If a model ID is given inline in a prompt, put it in `.env` as a default and
say so; **do not bake it into a module.** A model ID buried in a module is invisible when it is
withdrawn, and Preview IDs are withdrawn without notice.

### 7. Runtime-only transitive dependencies stay pinned
Dependencies required at runtime by transitive libraries stay pinned in `api/pyproject.toml` with a
comment naming why, **even when nothing in this codebase imports them.** `requests` is one such:
`google-auth`'s ADC token refresh imports it at runtime, and removing it breaks credential refresh
with `The requests library is not installed`. **Do not prune the dependency list based on static
import analysis alone** — verify with `make smoke`, which exercises real credential refresh.

---

## DEFAULT: commit and push at the end of every prompt

Commit at the end of every prompt with a conventional-commit message, and push. **Do not ask first.**

**Exception:** if the tree contains changes that were not asked for, or work you consider incomplete
or unverified, **stop and say so instead of committing.** Report what is uncommitted and why.

---

## SCOPE RULE

**Do not add features that were not asked for.** No auth, no database, no user accounts, no deployment
config, no Docker — unless explicitly requested. When a task is ambiguous, implement the smaller thing
and ask.

**`/health` is not an endpoint yet.** `health_snapshot()` in `api/rxconcile/gcp/health.py` returns the
data; prompt 6 wraps it in a route. **Do not add an HTTP layer before then.**

---

## Style

**Python 3.11**, full type hints on every function, pydantic v2 for all data models, pytest for tests.
`mypy --strict` must pass.

**TypeScript strict**, no `any`. `strict: true` is enabled in `web/tsconfig.app.json` and
`web/tsconfig.node.json`.

---

## Configuration

Config lives in `.env` (gitignored); `.env.example` documents the keys.

| Key | Value |
| --- | --- |
| `GCP_PROJECT_ID` | `rxconcile-28x2` |
| `GCP_LOCATION` | `global` |
| `GEMINI_MODEL` | `gemini-3.7-flash` |
| `GEMINI_MODEL_FALLBACK` | `gemini-3.1-pro-preview` |
| `MAX_UPLOAD_MB` | `15` |

Model IDs were verified against the live Vertex AI API on **2026-08-27**. Re-verify with
`make list-models` before changing them — published docs have lagged the actual API.

**Endpoint caveat:** Gemini 3.x models resolve only on the `global` endpoint. `us-central1` returns
404 for `gemini-3.7-flash`. Do not treat a region as a drop-in fallback without re-checking.

**Pro-tier caveat:** `gemini-3.1-pro-preview` is a Preview offering under Pre-GA terms; its ID may
change. There is no `gemini-3-pro` or `gemini-3.1-pro` — both 404.
