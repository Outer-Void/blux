# Changelog

Versioning: the contract version (`MAJOR.MINOR`, see ADR-0005) and package versions (semver) move independently.

## Unreleased: Stage 03
- `provider.kind: subprocess` capabilities (ADR-0009): `blux_runtime.subprocess_provider.SubprocessProvider` (argv, stdin request, one JSON object out, minimal env, stderr discarded, timeout kill).
- `blux_runtime.project.register_project` and `blux run|exec|capabilities --project DIR` load a repository's `.blux/` capabilities; the project policy applies unless `--policy` is given.
- Handlers may raise `OUTPUT_VALIDATION_FAILED` (a failure, still never a rejection).
- Hard-gate integration tests with DAT and BLUX System (`tests/integration/test_dat.py`); CI requires them.

## Unreleased: Stage 02
- `blux_runtime.system_sink.BluxSystemSink` records receipts in BLUX System through the `blux-system` CLI. It spools to a local outbox when the store is unavailable, `flush()` retries, and refused receipts are kept in `*.refused.jsonl`.
- `Runtime.execute` never loses a result when a sink fails. `Outcome.sink_error` reports the failure.
- `blux run|exec --system-store DIR` records the receipt and fails closed (exit 1, result still on stdout) if it cannot.
- Integration tests (`tests/integration/`) run echo.test → BLUX System → graph/timeline/replay, plus failed, denied and cancelled operations and secret redaction. CI requires them.
- Tag `blux-sdk-v0.1.0` marks the SDK commit pinned by blux-system 2.0.0.

## Stage 01
### Contract v1.0
- Schemas: capability, capability-set, execution, result, receipt, artifact, model, project, policy, error, plus common definitions.
- Universal IDs: `op_`/`rcpt_`/`art_`/`ses_` ULIDs; declared `project_id`, `capability_id`, `model_id`.
- Schema checksum lock (`contracts/v1/CHECKSUMS.sha256`).

### blux-sdk 0.1.0
- Envelope gate with explicit codes for malformed and unsupported versions.
- Strict JSON/YAML parsing, canonical JSON + SHA-256, ULID generation, envelope builders, error normalization, `CapabilityDescriptor`, `.blux/` loader.
- Schemas shipped as package data.

### outervoid-blux 0.1.0
- Reference runtime: registry, default-deny policy engine, executor that always emits a result and a receipt, `echo.test`.
- `blux` CLI: `run`, `exec`, `validate`, `capabilities`, `project`, `id`, `version`.

### Stage 00
- Ecosystem baseline in `docs/ecosystem/`.
