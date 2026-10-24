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
