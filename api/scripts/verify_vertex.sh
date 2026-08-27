#!/usr/bin/env bash
# Prove the Vertex chain works: auth -> endpoint -> model -> multimodal.
# Sends a generated PNG inline as base64 and checks the transcription.
set -euo pipefail
cd "$(dirname "$0")/../.."
set -a; . ./.env; set +a

MODEL="${1:-$GEMINI_MODEL}"
if [ "$GCP_LOCATION" = "global" ]; then HOST="aiplatform.googleapis.com";
else HOST="${GCP_LOCATION}-aiplatform.googleapis.com"; fi
URL="https://${HOST}/v1/projects/${GCP_PROJECT_ID}/locations/${GCP_LOCATION}/publishers/google/models/${MODEL}:generateContent"
TOKEN=$(gcloud auth print-access-token)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "project=${GCP_PROJECT_ID} location=${GCP_LOCATION} model=${MODEL}"

echo "[1/2] text..."
curl -sS -X POST "$URL" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"contents":[{"role":"user","parts":[{"text":"Reply with exactly: OK"}]}],"generationConfig":{"maxOutputTokens":2000}}' \
| python3 -c "
import json,sys
d=json.load(sys.stdin)
if 'error' in d: print('  FAIL:', d['error'].get('status'), d['error'].get('message')[:200]); sys.exit(1)
t=''.join(p.get('text','') for c in d.get('candidates',[]) for p in c.get('content',{}).get('parts',[]))
print('  ok:', repr(t.strip()))
"

echo "[2/2] multimodal..."
python3 - "$TMP/t.png" <<'PY'
import sys
from PIL import Image, ImageDraw, ImageFont
TEXT="PARACETAMOL 500"
f=None
for p in ["/System/Library/Fonts/Supplemental/Arial Bold.ttf","/System/Library/Fonts/Helvetica.ttc"]:
    try: f=ImageFont.truetype(p,76); break
    except Exception: pass
if f is None: f=ImageFont.load_default()
l,t,r,b=ImageDraw.Draw(Image.new("RGB",(10,10))).textbbox((0,0),TEXT,font=f)
P=50; img=Image.new("RGB",((r-l)+2*P,(b-t)+2*P),"white")
ImageDraw.Draw(img).text((P-l,P-t),TEXT,fill="black",font=f)
img.save(sys.argv[1])
PY

python3 - "$TMP/t.png" "$TMP/req.json" <<'PY'
import base64,json,sys
b64=base64.b64encode(open(sys.argv[1],'rb').read()).decode()
json.dump({"contents":[{"role":"user","parts":[
  {"inline_data":{"mime_type":"image/png","data":b64}},
  {"text":"Transcribe the text visible in this image exactly. Output only the transcribed text."}]}],
  "generationConfig":{"maxOutputTokens":2000}}, open(sys.argv[2],'w'))
PY

curl -sS -X POST "$URL" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  --data-binary @"$TMP/req.json" \
| python3 -c "
import json,sys
d=json.load(sys.stdin)
if 'error' in d: print('  FAIL:', d['error'].get('status'), d['error'].get('message')[:200]); sys.exit(1)
t=''.join(p.get('text','') for c in d.get('candidates',[]) for p in c.get('content',{}).get('parts',[])).strip()
mods=[m.get('modality') for m in d.get('usageMetadata',{}).get('promptTokensDetails',[])]
print('  transcription:', repr(t)); print('  modalities:', mods)
sys.exit(0 if 'PARACETAMOL 500' in t else 1)
"
echo "PASS"
