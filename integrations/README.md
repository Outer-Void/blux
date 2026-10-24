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

No production repository is integrated in Stage 01. Per-repository adapters land in later stages, with BLHermes
explicitly deferred.

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
