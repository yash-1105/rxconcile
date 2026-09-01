# Deployment

Two halves, deployed separately.

| | URL | Platform |
| --- | --- | --- |
| Frontend | https://rxconcile.vercel.app | Vercel (project `rxconcile`, Root Directory `web`) |
| API | https://rxconcile-api-production.up.railway.app | Railway (project `rxconcile`, service `rxconcile-api`, Root Directory `api`) |

`https://rxconcile-yash-1105s-projects.vercel.app` is a second stable alias for
the same deployment. Both are in `ALLOWED_ORIGINS`; a per-deployment URL such as
`rxconcile-b3a8vizzx-….vercel.app` is **not**, so a preview build cannot call the
production API until its origin is added.

**This is a proof of concept, not a medical device, and the login is a demo
login.** Two hardcoded accounts, readable by anyone who opens devtools. Being
reachable on a public URL makes that less defensible, not more.

---

## Environment variables — API (Railway)

| Variable | Value | What it does |
| --- | --- | --- |
| `GCP_PROJECT_ID` | `rxconcile-28x2` | Project owning the Vertex AI quota. |
| `GCP_LOCATION` | `global` | Vertex endpoint. Only `global` is accepted — Gemini 3.x publisher models resolve nowhere else, so a regional value is a configuration error rather than a fallback. |
| `GEMINI_MODEL` | `gemini-3.7-flash` | Primary extraction model. |
| `GEMINI_MODEL_FALLBACK` | `gemini-3.1-pro-preview` | Pro-tier escalation for hard documents. Not used for quota retries. |
| `GEMINI_MODEL_QUOTA_FALLBACK` | `gemini-3.6-flash` | Used only when the primary is quota-exhausted. Same tier on purpose: a quota retry must not silently become a capability downgrade. |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | the key JSON | The service account key itself. Parsed in memory, never written to disk. |
| `DATABASE_PATH` | `/data/rxconcile.db` | SQLite file **on the mounted volume**. Without this the default resolves from the installed package location, which in a container is a path that means nothing — and would be wiped every deploy. |
| `ALLOWED_ORIGINS` | the two Vercel aliases, comma-separated | Browser origins allowed to call the API. Anything not listed is refused. |
| `MAX_UPLOAD_MB` | `15` | Largest accepted upload. |
| `PORT` | set by Railway | Bound by the start command, which falls back to 8000 locally. |

No model ID is hardcoded in Python (hard rule 6), so every one of the three
model variables is **required** — the API will not boot without them.

`GOOGLE_APPLICATION_CREDENTIALS` (no `_JSON`) is **rejected on sight** in both
`config.py` and `gcp/client.py`. That variable names a key *file on disk*, which
hard rule 9 forbids. The `_JSON` variable carries the material itself, so there
is no file to commit and none to leak.

## Environment variables — frontend (Vercel)

| Variable | Value | What it does |
| --- | --- | --- |
| `VITE_API_URL` | `https://rxconcile-api-production.up.railway.app` | Baked into the bundle at build time. |

**Production builds refuse to run without it** (`web/vite.config.ts`). That is
deliberate: unset, the bundle falls back to same-origin, so every `/api` call
hits Vercel and 404s — a build that succeeds and a site that does not work. Left
unset in development, where the Vite dev proxy makes same-origin correct.

---

## Service account

```
rxconcile-api@rxconcile-28x2.iam.gserviceaccount.com
```

Exactly one role, and it must stay that way:

```
roles/aiplatform.user
```

That is the whole grant. It calls Gemini and does nothing else — no Editor, no
Owner. Verify with:

```bash
gcloud projects get-iam-policy rxconcile-28x2 \
  --flatten="bindings[].members" \
  --filter="bindings.members:rxconcile-api@rxconcile-28x2.iam.gserviceaccount.com" \
  --format="value(bindings.role)"
```

**A fresh grant takes a minute or two to propagate.** A `403 PERMISSION_DENIED`
on `aiplatform.endpoints.predict` immediately after granting is almost always
propagation lag, not a wrong role. Check the role first with the command above,
then retry — **do not widen the role to make an error go away.**

---

## Volume

| | |
| --- | --- |
| Name | `rxconcile-api-volume` |
| Mount path | `/data` |
| Size | 5 GB |
| Holds | `/data/rxconcile.db` — every scan, decision and allowance |

Volumes mount **at runtime only** — not during the build and not in a
pre-deploy step. Everything that touches the database therefore happens in the
FastAPI lifespan, which runs as part of the start command. There is no
`preDeployCommand` and no build step that opens the database; do not add one.

The lifespan runs the schema migration on every boot. It is additive and
idempotent by construction: `create_all` skips existing tables,
`_add_missing_columns` skips any column already in `PRAGMA table_info`, and
`backfill` writes only where the target column is still empty.

### ⚠ Redeploying causes downtime — do not redeploy during a demo

A volume attaches to **one container at a time**, so Railway cannot run the old
and new containers side by side. The service goes down while they swap.

**Measured: 8 seconds of 502s**, plus roughly **40 seconds** for a cold start
before that container serves — the boot runs the migration, resolves
credentials, and calls Vertex to verify all three model IDs.

Setting *any* Railway environment variable triggers a redeploy, and so incurs
the same downtime. Change variables before a demo, never during one.

---

## Redeploying

**API:**

```bash
cd ~/projects/rxconcile
railway up                 # upload + build + deploy
railway redeploy -y        # same image, fresh container (no rebuild)
```

`railway up` uploads from the **repository root** regardless of the directory
you run it in; `.railwayignore` at the root keeps the 360 MB virtualenv and the
local database out of it. The service's Root Directory (`api`) scopes the
*build*, and it is settable only in the Railway dashboard — not via the CLI and
not in any config file.

Build config lives in `api/railpack.json` (start command). Healthcheck path
`/health` is a dashboard setting. **`railway.json` is deliberately absent:**
Railway deprecated Config-as-Code and the file is not merely discouraged, it is
ignored — a deploy failed with "No start command detected" while it sat in the
repo looking authoritative. The supported replacement, `.railway/railway.ts`,
needs an npm SDK at the repo root; that decision is still open, with a
2026-12-01 deadline.

**Frontend:**

```bash
cd ~/projects/rxconcile/web
npx vercel --prod --yes
```

Vercel's Root Directory is `web`. `web/vercel.json` carries the build, the
output directory, and the SPA rewrite that stops a refresh on a client-side
route 404ing.

---

## Rotating the service account key

The key is a static credential with no expiry. Rotate it if it is ever exposed,
and periodically regardless.

```bash
# 1. New key, written OUTSIDE the repository.
gcloud iam service-accounts keys create ~/rxconcile-key.json \
  --iam-account=rxconcile-api@rxconcile-28x2.iam.gserviceaccount.com

# 2. Prove it works BEFORE it reaches production. A key that fails after
#    deployment is far harder to diagnose than one that fails now.
cd ~/projects/rxconcile
GOOGLE_APPLICATION_CREDENTIALS_JSON="$(cat ~/rxconcile-key.json)" \
  api/.venv/bin/python api/scripts/smoke_gcp.py    # must print PASS

# 3. Install it. --stdin keeps the key off the command line, out of the shell
#    history, and out of any terminal transcript.
railway variables set --service rxconcile-api --stdin \
  GOOGLE_APPLICATION_CREDENTIALS_JSON < ~/rxconcile-key.json
#    (this triggers a redeploy — see the downtime warning above)

# 4. Confirm the new key is the one serving.
curl -s https://rxconcile-api-production.up.railway.app/health

# 5. Retire the old key by its id, then delete the local file.
gcloud iam service-accounts keys list \
  --iam-account=rxconcile-api@rxconcile-28x2.iam.gserviceaccount.com
gcloud iam service-accounts keys delete <OLD_KEY_ID> \
  --iam-account=rxconcile-api@rxconcile-28x2.iam.gserviceaccount.com
rm ~/rxconcile-key.json
```

**Never** `cat` the key, paste it into a terminal, commit it, or write it
anywhere inside the repository. `~/rxconcile-key.json` is outside the repo on
purpose. Confirm the repo is clean at any time with:

```bash
git ls-files | grep -i key         # expect no key files
git log --all -S'BEGIN PRIVATE KEY' --oneline    # expect nothing
```

---

## Health

```bash
curl -s https://rxconcile-api-production.up.railway.app/health
```

Returns the project, location, all three model IDs, and
`models_verified_at_startup`. That flag is not decorative: the boot calls Vertex
and asserts every configured model resolves, so a withdrawn Preview ID fails the
deploy rather than a reconciliation mid-demo. Verified by deploying a bad ID on
purpose — the container refused to serve.

`/health` touches neither the database nor Vertex once running, so a passing
healthcheck says the process is alive, not that the volume is mounted. To check
the data, read a scan back.

## Expected timings

| | |
| --- | --- |
| `/health` | ~0.5 s (mostly network round-trip) |
| Reconciliation, uncached | 19–26 s — three extraction runs per document against Gemini |
| Reconciliation, cached | under 1 s |
| Cold start | ~40 s |
| Redeploy downtime | ~8 s |

The service does not sleep: after five minutes idle the first request was
indistinguishable from a warm one.
