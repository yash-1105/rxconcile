# rxconcile

OCRs a handwritten doctor's prescription and a pharmacy bill, extracts structured data from both, and
reports whether the medicines dispensed match what was prescribed.

**Proof of concept. Not a medical device. Reports document discrepancies only — no medical advice, no
dosing recommendations, no clinical judgement.**

## Design in one line

Gemini multimodal does **extraction only**; every match/mismatch verdict comes from deterministic
Python in `api/rxconcile/reconcile/`. See [CLAUDE.md](./CLAUDE.md) for the hard rules.

## Layout

```
api/
  rxconcile/            Python package
    reconcile/          deterministic matching — all verdicts originate here
  tests/                pytest
  scripts/              Vertex verification helpers
web/                    React 19 + TypeScript + Vite
samples/                sample prescriptions / bills
docs/
```

## Prerequisites

- Google Cloud SDK, authenticated (`gcloud auth login`)
- Python 3.11+
- Node 20+

## Setup

```bash
cp .env.example .env     # then fill in GCP_PROJECT_ID
make setup
```

`make setup` creates `api/.venv`, installs the package with dev extras, and runs `npm install`.

## Verify the Vertex chain

Before writing any application code, confirm auth → endpoint → model → multimodal all work:

```bash
make verify        # text call + inline-base64 PNG transcription
make list-models   # what this project can actually reach
```

`make verify` passes only if the model transcribes a generated `PARACETAMOL 500` image, which proves
multimodal works rather than just text.

## Common tasks

```bash
make test        # pytest
make typecheck   # mypy --strict + tsc
make lint        # ruff + oxlint
make dev         # web dev server
```

## Known limitations

Measured, not assumed. See [docs/BASELINE.md](./docs/BASELINE.md) for the
evidence and [docs/DESIGN_DECISIONS.md](./docs/DESIGN_DECISIONS.md) for what
follows from it.

- **Model confidence scores do not gate anything.** Measured range across 56
  observations was 0.75–0.95, with the highest score (0.95) appearing on a field
  the model could not reproduce across three runs. Treat `confidence` and
  `overall_legibility` as model-reported numbers, not as reliability signals.

- **Scope: English/Latin script only.** Input is assumed to be English-language
  prescriptions and bills in Latin script. Documents in other scripts may extract
  with reduced reliability and are **not validated** — this is a deliberate scope
  exclusion, not a pending fix. Measured on Bengali input, the model transcribes
  legible script but *generates* plausible script where the handwriting is
  unclear, with no signal distinguishing the two: one duration was read as
  "2 weeks", "1 week" and "7 days" across runs, each at confidence 0.85. That is
  invisible to a reviewer who does not read the script. (Bengali digits and three
  duration words are handled in the sig parser as a narrow, documented
  exception.)

- **Item counts can vary between runs.** One document returned 7, 7 and 6 items
  across three extractions, silently dropping a prescribed line. A dropped line
  produces no discrepancy finding, so it reads as a clean match.

- **Accuracy is unmeasured.** Ground truth for drug names is unconfirmed. What
  has been measured is reproducibility; a field can be stable and still wrong.

**This is a proof of concept. Do not use it to make any decision about a
patient's medication.**

## Configuration

| Key | Default | Notes |
| --- | --- | --- |
| `GCP_PROJECT_ID` | — | your project |
| `GCP_LOCATION` | `global` | see caveat below |
| `GEMINI_MODEL` | `gemini-3.7-flash` | default extraction model |
| `GEMINI_MODEL_FALLBACK` | `gemini-3.1-pro-preview` | escalation; Preview status |
| `MAX_UPLOAD_MB` | `15` | |

Model IDs verified against the live API on **2026-08-27**. Published docs have lagged the real API —
run `make list-models` rather than trusting a doc page.

**Endpoint caveat:** Gemini 3.x resolves only on the `global` endpoint; `us-central1` returns 404 for
`gemini-3.7-flash`. A region is not a drop-in fallback.

Cloud Vision and Document AI are deliberately **not** enabled on the project. This build is Gemini-only
and passes image bytes inline, so no Cloud Storage bucket is involved.
