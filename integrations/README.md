# integrations/

Integration metadata and specifications for repositories joining the federation.

To integrate a repository:
1. Add `.blux/` per [`docs/spec/dot-blux.md`](../docs/spec/dot-blux.md) and choose `role: standalone | integrated` (ADR-0002).
2. Declare each capability honestly: side effects, permissions, I/O schemas.
3. Expose it via `provider.kind`:
   - `python`: an importable `module:function` handler using `blux-sdk`;
   - `subprocess`: a command printing exactly one JSON object on stdout (ADR-0008).
4. Validate with `blux project <repo>` in that repository's CI.
5. Register its capabilities in `registry/capabilities.yaml` here.

Integrated so far: **blux-system** (Stage 02, receipts) and **DAT** (Stage 03, seven capabilities). BLHermes
remains explicitly deferred.

## BLUX System (Stage 02)
Receipt persistence belongs to [blux-system](https://github.com/Outer-Void/blux-system) (ADR-0002).
`blux_runtime.system_sink.BluxSystemSink` hands each receipt to `blux-system ingest` over the
one-JSON-document CLI protocol (ADR-0008). This repository never imports blux-system code.

```sh
blux-system init --store ./truth
blux run echo.test --input '{"message":"hi"}' --system-store ./truth
blux-system graph --store ./truth
```

| Situation | Behaviour |
|---|---|
| Stored, or already stored | ok |
| Store unavailable (not found, locked, timeout, executable missing, protocol error) | Receipt appended to `<store>.outbox.jsonl`, `Outcome.sink_error.spooled = true`. `BluxSystemSink.flush()` retries later |
| Store refuses the receipt (invalid, conflict) | Not spooled. `sink_error` carries the store's error document |
| CLI with `--system-store` | Prints the result, then exits 1 with a stderr note if the receipt was not recorded |

blux-system depends on blux-sdk at tag `blux-sdk-v0.1.0` (`0b09a46`). Bump that pin deliberately when Contract v1 changes.

## DAT (Stage 03)
[DAT](https://github.com/Outer-Void/dat) is `role: standalone`. Its `.blux/` declares `repo.audit`,
`repo.security`, `repo.quality`, `repo.release`, `package.audit`, `artifact.audit` and `policy.verify`, all
read-only, all `provider.kind: subprocess` (`dat blux-capability <id>`, ADR-0009).

```sh
blux run repo.release --project path/to/dat --input '{"target": "."}' --system-store ./truth
```

| DAT | BLUX result | BLUX System |
|---|---|---|
| Audit completed (gate pass *or fail*) | `succeeded`; `output` is the `dat.report`, `output.gate.verdict` is the verdict | `SUCCESS` |
| DAT could not audit (missing target, invalid policy, analyzer failure) | `failed`, `EXECUTION_FAILED`, `details.provider_code` = DAT's code | `FAILURE` |

`output.linkage.operation_id` equals the BLUX operation id, and the receipt's `output_sha256` is the hash
of the report. Tests: `tests/integration/test_dat.py`. CI installs DAT at a pinned commit.

## LRC (Stage 04)
[LRC](https://github.com/Outer-Void/lrc) is `role: standalone`. Its `.blux/` declares `repo.compile` and
`repo.validate` (read), `repo.create` and `repo.scaffold` (write) and `repo.migrate` (destructive; the default
policy allows it for users only). All are `provider.kind: subprocess` (`lrc blux-capability <id>`).

LRC never calls DAT through BLUX. A create/scaffold/migrate result carries
`handoff: {"tool": "dat", "capability_id": "repo.audit", "input": {"target": ...}}`; run it as a child:

```sh
blux run repo.create --project path/to/lrc --actor-type service \
  --input '{"spec_file": "tests/fixtures/hardgate.lrc", "target": "fixture"}' --system-store ./truth > create.json
blux run repo.audit --project path/to/dat --actor-type service --input "$(jq -c .output.handoff.input create.json)" \
  --parent-operation-id "$(jq -r .operation_id create.json)" --system-store ./truth
```

| LRC | BLUX result | BLUX System |
|---|---|---|
| Repository created / applied (or validation verdict pass *or fail*) | `succeeded` | `SUCCESS` |
| LRC could not act (invalid plan, target exists, migration conflicts) | `failed`, `EXECUTION_FAILED`, `details.provider_code` = LRC's code | `FAILURE` |

The fixture is reproducible: its `plan_id` and initial git commit are pinned in LRC's
`tests/fixtures/hardgate.expected.json`, and the hard-gate test checks them. Tests: `tests/integration/test_lrc.py`.

## GOD (Stage 05)
[GOD](https://github.com/Outer-Void/god) is `role: integrated`: it reads `.blux/` through blux-sdk (no BLUX
runtime). Its `.blux/` declares `capability.discover`, `capability.describe`, `capability.compat`,
`project.describe` and `command.discover` (read-only) and `registry.index` (writes GOD's own cache). GOD owns
discovery, not orchestration: it never runs a capability or a discovered program and never evaluates policy.

Use it to find a provider, then run the provider through BLUX:

```sh
blux run capability.discover --project path/to/god --input '{"roots": ["~/src"], "query": "repo.create"}' > d.json
blux run repo.create --project "$(jq -r '.output.capabilities[0].project_root' d.json)" \
  --parent-operation-id "$(jq -r .operation_id d.json)" --input '{...}'
```

Malformed manifests and duplicates come back in `problems`/`duplicates` (a `succeeded` result); an unknown or
ambiguous capability is `failed` with `provider_code` `NOT_FOUND`/`AMBIGUOUS`. Tests: `tests/integration/test_god.py`.


## CogA (Stage 06)
[CogA](https://github.com/Outer-Void/blux-coga) is `role: standalone`. Its `.blux/` declares seven
side-effect-free capabilities: `reason.analyze`, `reason.plan`, `reason.compare`, `reason.decompose`,
`reason.critique`, `reason.rank` and `reason.risk`. Each one's `input_schema`/`output_schema` is the JSON
Schema CogA enforces itself, so BLUX validates every request and artifact. Artifacts are explicit,
deterministic reasoning structures (evidence refs, confidence with basis, uncertainty sources, dataset
links, replay metadata), never model chain-of-thought.

```sh
blux run reason.compare --project path/to/blux-coga --input "$(cat req.json)" --system-store ./truth > r.json
jq .output r.json > art.json && coga replay art.json --request req.json -o re.json
blux-system replay-check "$(jq -r .operation_id r.json)" --store ./truth --input req.json --output re.json
```

| CogA | BLUX result | BLUX System |
|---|---|---|
| artifact | `succeeded`, `output` = `coga.artifact` | `SUCCESS`; `replay-check` reproduces it |
| request fails the input schema | `rejected`, `SCHEMA_VALIDATION_FAILED` (CogA never runs) | `FAILURE` |
| CogA cannot reason about it (cycle, unknown reference) | `failed`, `EXECUTION_FAILED`, `provider_code` | `FAILURE` |

Tests: `tests/integration/test_coga.py`.
