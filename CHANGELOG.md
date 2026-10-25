# Changelog

Versioning: the contract version (`MAJOR.MINOR`, see ADR-0005) and package versions (semver) move independently.

## Unreleased: Stage 10
- **Direct `blux` CLI** (ADR-0011, docs/cli.md). `outervoid-blux` 0.3.0; the SDK is unchanged at 0.2.0.
  - `blux "..."` / `blux ask`: a logical model through the Model API, with piped stdin as the input
    (`git diff | blux review`).
  - `blux chat`: interactive on a terminal, one turn per line when piped. Turns share a session, and each
    links to the previous one.
  - `blux doctor`: config, routes, backends and credential presence (never values); `--probe` calls
    every route.
  - `blux config`: effective settings and their sources.
  - **Modes.** Human mode streams to a terminal and prints a footer on stderr. Machine mode (`--json`)
    prints `blux.result` documents.
  - **Exit codes.** 0 ok, 1 model failure, 2 usage, 3 configuration, 4 timeout, 5 receipt not recorded,
    130 cancelled. `--timeout`, and Ctrl-C cancels the backend.
  - **Safety.** Credential-like settings in the config file are refused, and credential-shaped input is
    redacted before sending (`--no-redact`).
  - **Boundary.** No project, device or ecosystem commands: that is `ax`.
  - **Dependencies.** `tomli` on Python 3.10 only.
  - **Tests.** In-process (`tests/cli/test_direct.py`) and a hard gate against the real BluxGPT process
    and BLUX System (`tests/integration/test_cli_direct.py`, plus a REAL provider path with
    `BLUX_REAL_MODELS=1`).

## Unreleased: Stage 09
- **License.** The BLUX Proprietary License applies from 2026-10-25
  (Copyright (c) 2026 Outer-Void / Justadudeinspace (JADIS); contact outervoid.blux@gmail.com, fallback
  theoutervoid@outlook.com). Package metadata declares the matching `LicenseRef`. Copies and releases
  obtained before that date keep the Apache License 2.0.
- **BLUX Model API v1** (ADR-0010, docs/model-api/v1.md, `contracts/model-api/v1/` with its own checksum
  lock).
  - **Logical models.** `blux-main`, `blux-fast`, `blux-reason` and `blux-code`. Callers never name a
    vendor.
  - **Schemas.** Request, response, error, stream-event, routing table and catalog.
  - **Request features.** Messages, system and context; streaming; structured output; tools and tool
    calls; capability requirements; deterministic controls where providers expose them; timeouts;
    cancellation.
  - **Normalized errors.** Each code fixes whether a retry later may succeed and whether another route may
    serve the request now (`retryable`/`fallback`); content filtering never falls back.
- **SDK 0.2.0.** `blux_sdk.model_api` adds validation, error types and a credential scrubber. Contract v1 is
  unchanged: same schemas, same lock.
- **Runtime 0.2.0.**
  - **Router.** `blux_runtime.models.ModelRouter` handles capability-based route selection, explicit
    fallback and degradation, structured-output and tool-call validation, deadlines and cancellation.
  - **Backends.** Subprocess model backends speak a JSON-Lines event protocol; children get a minimal
    environment plus only the route's `credential_env`.
  - **Defaults.** A packaged routing table, and a model policy.
- **Capabilities.** Built-in `model.generate` (`external`, `net:model`) and `model.catalog`. Each
  `model.generate` receipt lists a `model-call` artifact of safe metadata (provider, route, attempts,
  usage, timing, determinism) with no prompt, output text or credentials.
- **Executor (backward compatible).** Handlers may `attach_artifact`, `emit_event` and observe `cancel`;
  `Runtime.execute(on_event=, cancel=)`; sinks may `emit_artifact` (BluxSystemSink ingests the artifact with
  its bytes).
- **CLI.** `blux models`, `blux generate MODEL PROMPT [--events FILE] [--json-schema F] [--deterministic]
  [--no-fallback] [--system-store S]`.
- **Hard gate** (`tests/integration/test_model_api.py`, real BluxGPT backend process):
  - a caller targets a logical model without knowing the provider;
  - provider failures degrade (with every attempt recorded), route around a missing backend, or fail
    explicitly (`ALL_ROUTES_FAILED`, `CONTENT_FILTERED`, a hung backend killed at the deadline);
  - BLUX System holds the receipt and artifact but neither the prompt, the output nor any credential.

  It is mocked by default; `BLUX_REAL_MODELS=1` adds a REAL provider path through the default routes.
  CI installs BluxGPT at a pinned commit and requires the tests.
- `blux capabilities` now lists `model.catalog` and `model.generate`; the reference policy still allows
  only `echo.test`.

## Unreleased: Stage 08
- CI pins cA 2.0.1 (6dcfcd7), which fixes a crash on a path through an existing file; the bug was found by the Stage 08 blux-ca-dataset build.

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
