# Changelog

Versioning: the contract version (`MAJOR.MINOR`, see ADR-0005) and package versions (semver) move independently.

## Unreleased: Stage 07
- Hard gate: CogA `reason.plan` -> cA `artifact.construct` (from the plan artifact, in plan order) -> DAT `repo.quality` fails the build -> cA `artifact.repair` (attempt 1 of the default 2, from DAT's report) -> DAT `repo.quality` passes, all as parent/child operations in one session; BLUX System reconstructs the chain (one root, four edges, every node SUCCESS, verified log of five records, receipts matching each output). cA's results link back: the construction names the plan's operation and the repair names the DAT audit it worked from (`tests/integration/test_ca.py`). Also: a third repair attempt is refused (`REPAIR_LIMIT_REACHED`, recorded as a failure), agents can check (`artifact.validate`) but not write (`POLICY_DENIED`), and cA's preserved rollback artifacts undo the repair afterwards. CI installs cA at a pinned commit and requires the tests (Python 3.11+).

## Unreleased: Stage 06
- Hard gate: BLUX invokes every CogA `reason.*` capability (analyze, plan, compare, decompose, critique, rank, risk); each artifact validates against the capability's output schema (the schema CogA itself enforces), equals the artifact id standalone CogA pins in its eval suite, and replay-links through BLUX System: `coga replay` recomputes the stored output and `blux-system replay-check` confirms the receipt's input and output hashes (`tests/integration/test_coga.py`). Also: a tampered artifact does not replay, the same request in two operations links by `artifact_id`, and a schema rejection (`rejected`, before CogA runs) stays distinguishable from a CogA failure (`failed`, `provider_code`). CI installs CogA at a pinned commit and requires the tests on every Python version.

## Unreleased: Stage 05
- Hard gate: BLUX starts knowing only GOD, asks it (`capability.discover`) who provides `repo.create`, loads the project GOD names, then LRC creates the reproducible fixture and DAT audits it as linked child operations; BLUX System reconstructs the chain (one root, two edges, timeline, verified log, receipts matching each output) (`tests/integration/test_god.py`). Also: GOD's findings (malformed manifests, duplicates) are results while an ambiguous lookup is a recorded FAILURE, and GOD stays read-only (a discovered program is never run). CI installs GOD at a pinned commit.

## Unreleased: Stage 04
- Hard gate: BLUX -> LRC `repo.create` builds a reproducible fixture repository -> DAT `repo.audit` runs as its child operation (LRC's result names it in `handoff`) -> BLUX System records the chain (`tests/integration/test_lrc.py`). LRC and DAT load into one registry without capability-id collisions. CI installs LRC at a pinned commit and requires the tests.

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
