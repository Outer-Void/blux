# ADR-0006: Error model

- Status: Accepted (Stage 01, 2026-10-24)

## Decision
Errors are data. One wire shape (`blux.error`), one exception type (`BluxError`), and a closed code set:

| Code | Status | Retryable |
|---|---|---|
| MALFORMED_INPUT | rejected | no |
| INVALID_ENVELOPE | rejected | no |
| MALFORMED_CONTRACT_VERSION | rejected | no |
| UNSUPPORTED_CONTRACT_VERSION | rejected | no |
| SCHEMA_VALIDATION_FAILED | rejected | no |
| UNKNOWN_CAPABILITY | rejected | no |
| CAPABILITY_VERSION_MISMATCH | rejected | no |
| POLICY_DENIED | rejected | no |
| EXECUTION_FAILED | failed | no |
| TIMEOUT | failed | yes |
| OUTPUT_VALIDATION_FAILED | failed | no |
| INTERNAL_ERROR | failed | no |

- `code` is the contract. `message` is for humans and may change.
- **No leakage.** `normalize_error` reports only the exception *type*, never its text or a traceback. Schema errors report paths and keywords, never instance values.
- Handlers can fail only as `EXECUTION_FAILED` / `TIMEOUT`. A handler raising `POLICY_DENIED` is recorded as `EXECUTION_FAILED` with `details.handler_code`, so a capability cannot impersonate the runtime's decisions.
- Malformed input checks: invalid UTF-8, invalid JSON/YAML, duplicate keys, NaN/Infinity, non-object top level, payloads over 1 MiB, excessive nesting, and YAML types JSON cannot represent.
