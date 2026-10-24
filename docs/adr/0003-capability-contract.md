# ADR-0003: Capability contract

- Status: Accepted (Stage 01, 2026-10-24)

## Decision
Everything BLUX can ask a repository to do is a **capability**, described by a `blux.capability` document
(`contracts/v1/capability.schema.json`):

- `capability_id`: a declared dotted name (`namespace.name`), e.g. `echo.test`;
- `version`: semver of the implementation. Requests may pin it, and a mismatch is `CAPABILITY_VERSION_MISMATCH`;
- `contract_versions`: which contract versions it accepts;
- `input_schema` / `output_schema`: JSON Schema 2020-12. The runtime enforces both. Bad input is a rejection (`SCHEMA_VALIDATION_FAILED`); bad output is a failure (`OUTPUT_VALIDATION_FAILED`);
- `side_effects`: `none < read < write < external < destructive`;
- `required_permissions`: must be granted by policy (ADR-0007);
- `idempotent`, `timeout_ms`, and `provider` (repository, kind, entrypoint).

**Models are not invoked directly.** A `blux.model` document describes a model. Generation or embedding is
exposed as a capability that routes to a model, so it gets the same policy, receipts and schemas.

`echo.test` is the reference capability. It is pure, deterministic and side-effect free, and proves the full
round trip without depending on any production repository.
