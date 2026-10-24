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
