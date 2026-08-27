#!/usr/bin/env bash
# Enumerate Gemini models actually available to this project.
# Use this instead of trusting published docs — the docs have lagged the API.
set -euo pipefail
cd "$(dirname "$0")/../.."
set -a; . ./.env; set +a
TOKEN=$(gcloud auth print-access-token)
curl -sS -H "Authorization: Bearer ${TOKEN}" \
     -H "x-goog-user-project: ${GCP_PROJECT_ID}" \
     "https://aiplatform.googleapis.com/v1beta1/publishers/google/models?pageSize=300" \
| python3 -c "
import json,sys
d=json.load(sys.stdin)
if 'error' in d:
    print('ERROR:', d['error'].get('status'), d['error'].get('message')); sys.exit(1)
for n in sorted({m.get('name','').split('/')[-1] for m in d.get('publisherModels',[])}):
    if 'gemini' in n: print(n)
"
