# ADR-0004: Operation / result envelope

- Status: Accepted (Stage 01, 2026-10-24)

## Decision
Every contract document starts with `contract` (`blux.<kind>`) and `contract_version`.

One operation = one `blux.execution` request → one `blux.result` + one `blux.receipt`:

```
execution {operation_id, parent_operation_id, session_id, project_id, capability_id, actor, input, requested_at}
   │
   ▼  runtime: envelope → version → capability → pin → input schema → policy → handler → output schema
result  {operation_id, status: succeeded|failed|rejected, output|error, receipt_id, artifact_ids, timings}
receipt {receipt_id, operation_id, lineage ids, actor, policy_decision, input_sha256, output_sha256, error_code, issuer}
```

- **rejected**: refused before the handler ran (malformed, version, unknown capability, pin, input, policy).
- **failed**: the handler ran, then the operation failed (exception, timeout, bad output).
- Exactly one of `output` / `error` is non-null. The schema enforces this.
- The runtime **always** answers with a valid result and emits a valid receipt, even for unparseable bytes. If the request carries no valid `operation_id`, the runtime assigns one.
- `parent_operation_id` links sub-operations into a tree. `session_id` groups operations.
- Hashes are SHA-256 of canonical JSON (sorted keys, compact, UTF-8).
- Artifacts are content-addressed descriptors (`blux.artifact`). They describe outputs and store nothing.

### Universal IDs
| ID | Form | Origin |
|---|---|---|
| `operation_id`, `parent_operation_id` | `op_<ULID>` | generated |
| `receipt_id` | `rcpt_<ULID>` | generated |
| `artifact_id` | `art_<ULID>` | generated |
| `session_id` | `ses_<ULID>` | generated |
| `project_id` | slug, e.g. `void-os` | declared |
| `capability_id` | dotted, e.g. `echo.test` | declared |
| `model_id` | `provider/name` | declared |

ULIDs sort by creation time and need no coordination between repositories.
