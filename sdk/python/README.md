# blux-sdk (Python)

Reference implementation of BLUX Contract v1.

- `blux_sdk.schemas`: packaged v1 JSON Schemas, `check_envelope(doc, kind)` and `validate(kind, doc)`
- `blux_sdk.ids`: ULID-based generated IDs (`op_`, `rcpt_`, `art_`, `ses_`) and validators for declared IDs
- `blux_sdk.envelopes`: builders for execution requests, results, receipts and artifacts
- `blux_sdk.errors`: `BluxError`, the closed `ErrorCode` set, `normalize_error`
- `blux_sdk.versions`: `parse_version`, `require_supported`, `negotiate`
- `blux_sdk.serialization`: strict JSON/YAML parsing and canonical JSON + SHA-256
- `blux_sdk.capability`: `CapabilityDescriptor`
- `blux_sdk.dot_blux`: loader for a repository's `.blux/` directory

```python
from blux_sdk import envelopes, check_envelope

req = envelopes.execution_request("echo.test", {"message": "hi"}, actor={"type": "user", "id": "me"})
check_envelope(req, "execution")
```

The schemas in `src/blux_sdk/schemas/v1/` are generated from the canonical `contracts/v1/` tree
by `python packaging/sync_schemas.py`. Do not edit them by hand. CI fails if they drift.
