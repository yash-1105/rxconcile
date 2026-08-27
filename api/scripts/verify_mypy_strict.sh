#!/usr/bin/env bash
#
# Fail loudly if mypy is not actually running in strict mode.
#
# This exists because of a real regression: the repo root has no mypy config, so
# a bare `mypy api/...` silently used defaults while eight prompts in a row
# reported "mypy --strict clean". The code was substantially fine, but the claim
# was false for weeks.
#
# The check is behavioural rather than a config grep: it hands mypy a file that
# is only an error under strict settings. If mypy accepts it, strict is off.
set -uo pipefail

CONFIG="${1:?usage: verify_mypy_strict.sh <config-file> <python>}"
PYTHON="${2:?usage: verify_mypy_strict.sh <config-file> <python>}"

if [ ! -f "$CONFIG" ]; then
  echo "ERROR: mypy config not found at $CONFIG" >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cat > "$TMP/canary.py" <<'PY'
def unannotated(value):
    """Missing annotations: an error under strict, accepted by default."""
    return value
PY

if "$PYTHON" -m mypy --config-file "$CONFIG" "$TMP/canary.py" >/dev/null 2>&1; then
  cat >&2 <<MSG
ERROR: mypy accepted an unannotated function, so strict mode is NOT active.

  config file : $CONFIG
  meaning     : every "mypy strict clean" result from this target is worthless.

This has regressed before. The repo root has no mypy config, so mypy only picks
up api/pyproject.toml when --config-file points at it explicitly. Check that
[tool.mypy] strict = true is present and that the target passes --config-file.
MSG
  exit 1
fi

echo "mypy strict mode confirmed active ($CONFIG)"
