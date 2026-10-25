#!/usr/bin/env bash
# Build both wheels, install them into a fresh venv outside the source tree, and run the echo.test
# round trip from there. Proves schemas and policy ship as package data.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

"$PYTHON" -m build --wheel --outdir "$WORK/dist" "$ROOT/sdk/python" >/dev/null
"$PYTHON" -m build --wheel --outdir "$WORK/dist" "$ROOT" >/dev/null
"$PYTHON" -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install -q --find-links "$WORK/dist" outervoid-blux

cd "$WORK"
result="$("$WORK/venv/bin/blux" run echo.test --input '{"message":"wheel"}')"
echo "$result"
"$WORK/venv/bin/python" - "$result" <<'EOF'
import json, sys
from blux_sdk.schemas import check_envelope
doc = check_envelope(json.loads(sys.argv[1]), "result")
assert doc["status"] == "succeeded" and doc["output"]["message"] == "wheel", doc
import blux_sdk
assert "site-packages" in blux_sdk.__file__, blux_sdk.__file__
print("verify_wheel: OK", file=sys.stderr)
EOF
# Model API v1: packaged schemas, default routes and model policy load from the installed wheel.
"$WORK/venv/bin/blux" models > "$WORK/catalog.json"
"$WORK/venv/bin/python" - "$WORK/catalog.json" <<'EOF'
import json, sys
from blux_sdk import model_api
from blux_runtime.models import capability
doc = json.load(open(sys.argv[1]))
assert model_api.schema_errors("catalog", doc) == [] and [m["model"] for m in doc["models"]] == list(model_api.LOGICAL_MODELS), doc
assert model_api.schema_errors("routes", capability.default_routes()) == []
assert capability.model_policy()["policy_id"] == "blux-model-default"
print("verify_wheel: Model API OK", file=sys.stderr)
EOF
