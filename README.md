# BLUX

BLUX is the contract authority for the Outer-Void ecosystem. It defines the canonical contracts, SDKs, capability
registry, runtime and model interfaces, and the `blux` command line. Other repositories stay independent and
integrate through a `.blux/` directory (federated, not a monorepo; see ADR-0001).

## Status

| Component | Version | Source of truth |
|---|---|---|
| Runtime + CLI (`outervoid-blux`) | 0.4.0 | `pyproject.toml`, `runtime/blux_runtime/__init__.py`, `blux version` |
| Python SDK (`blux-sdk`) | 0.2.0 | `sdk/python/pyproject.toml` |
| Contract v1 / Model API v1 / Hook System v1 | `1.0` each | `contracts/*/CHECKSUMS.sha256` |

- **Development, not released.** There is no release tag or published package. The Termux `.deb` is built
  and audited in CI-style checks, but on-device installation through Termux APT has not been run
  ([packaging/termux/ACCEPTANCE.md](packaging/termux/ACCEPTANCE.md)).
- **Model API:** tested against the deterministic `mock` route and fake transports. Real-provider runs
  (OpenAI, Anthropic, DeepSeek) are not part of the recorded evidence.
- **BLHermes:** BLUX is the active replacement. The fork `Outer-Void/blhermes-agent` was **archived** by
  the owner after the Stage 17 review (final commit `dc8abc1db0`); see
  [docs/migration/blhermes-deletion-readiness.md](docs/migration/blhermes-deletion-readiness.md).
- **Devices:** BLUX can ask VoidForge for `device.*` actions; it is never the device authority
  ([ADR-0016](docs/adr/0016-voidforge-devices.md)).

`blux` is direct intelligence. The developer control plane is `ax` (AXIOM), which routes to BLUX and the
other engines. Ecosystem map and status matrix: [Outer-Void/docs `ecosystem/`](https://github.com/Outer-Void/docs/tree/main/ecosystem).

## Layout
| Path | Contents |
|---|---|
| `contracts/v1/` | Canonical JSON Schemas (locked by `CHECKSUMS.sha256`) |
| `contracts/model-api/v1/`, `contracts/hooks/v1/` | Model API v1 and Hook System v1 contracts (each with its own lock) |
| `sdk/python/` | `blux-sdk`: validation, IDs, envelopes, errors, serialization, version negotiation |
| `runtime/` | `blux_runtime`: capability registry, policy engine, executor, model router, hook bus (`hooks/`), state and sessions, security (approvals, command detection, path confinement), tools (`shell.exec`, `fs.read`, `fs.write`), extensions (skills, plugins, MCP, agents, scheduler, gateway), VoidForge device capabilities (`devices/`), legacy import (`compat/`), built-in `echo.test` |
| `cli/` | `blux_cli`: the `blux` command: direct intelligence (`blux "..."`, `ask`, `chat`, `doctor`, `config`; [docs/cli.md](docs/cli.md)), `agent`, `migrate`, and the Contract v1 machine commands |
| `intelligence/` | Scope of the direct BLUX intelligence surface |
| `registry/` | Federated capability index |
| `integrations/` | How repositories integrate |
| `migration/blhermes/` | BLHermes forensic inventory, parity matrix, characterization goldens and decoupling evidence (historical reference; MIT-derived data, see its UPSTREAM-NOTICE) |
| `training/` | Reserved; no training code yet |
| `packaging/` | Schema sync/lock, wheel verification, Termux `.deb` build/audit (`packaging/termux/`) |
| `tests/` | Contract, SDK, runtime, compatibility and CLI tests, plus v1 fixtures |
| `docs/` | ADRs, contract reference, `.blux/` spec, versioning, ecosystem baseline |
| `.blux/` | This repository's own BLUX manifest |

## Quick start
```sh
make install            # editable sdk + runtime/cli + dev tools
blux "explain this error"                 # direct intelligence (needs a model backend: BluxGPT)
cat build.log | blux "diagnose"
git diff | blux "review"
blux chat
blux doctor                               # config, routes, backends, credential presence
blux agent "summarize notes.txt" --tools fs.read   # agent loop, tools confined to the workspace
blux migrate hermes                       # dry run of a legacy BLHermes/Hermes state import (--apply to write)
blux run echo.test --input '{"message": "hello", "uppercase": true}'
blux exec examples/requests/echo.json --receipt-out /tmp/receipt.json
blux validate execution examples/requests/echo.json
blux project .
make ci                 # drift/lock check, format, lint, mypy --strict, tests, wheel round trip
```

## Decisions
[ADR-0001](docs/adr/0001-federated-architecture.md) federated architecture ·
[0002](docs/adr/0002-standalone-vs-integrated-boundary.md) standalone vs integrated ·
[0003](docs/adr/0003-capability-contract.md) capability contract ·
[0004](docs/adr/0004-operation-result-envelope.md) operation/result envelope ·
[0005](docs/adr/0005-version-negotiation.md) version negotiation ·
[0006](docs/adr/0006-error-model.md) error model ·
[0007](docs/adr/0007-authority-authorization-model.md) authority model ·
[0008](docs/adr/0008-no-arbitrary-stdout-protocol.md) no arbitrary stdout ·
[0009](docs/adr/0009-subprocess-provider.md) subprocess provider ·
[0010](docs/adr/0010-model-api.md) Model API v1 ·
[0011](docs/adr/0011-direct-cli.md) direct `blux` CLI ·
[0012](docs/adr/0012-hook-system.md) Hook System v1 ·
[0013](docs/adr/0013-blhermes-core-migration.md) BLHermes core migration ·
[0014](docs/adr/0014-blhermes-extensions.md) BLHermes extensions ·
[0015](docs/adr/0015-termux-packaging.md) Termux packaging ·
[0016](docs/adr/0016-voidforge-devices.md) VoidForge device capabilities

## Licensing and credits
See [CREDITS.md](CREDITS.md) and [docs/ecosystem/licensing.md](docs/ecosystem/licensing.md).
Outer Void is proprietary by default. Third-party rights remain third-party rights.

## Boundaries
- No web/UI layer here (BLUX App is a separate read-only viewer).
- No receipt persistence: blux-system owns it.
- No device authority: VoidForge owns it.
- The PyPI name `blux` belongs to an unrelated project, so the runtime distribution is `outervoid-blux`.

## Testing
`make ci` runs the schema lock check, `ruff format --check`, `ruff check`, `mypy`, `pytest` and the wheel
round trip. Last recorded run: 2026-10-26 at `f491524` (Stage 22), 563 passed and 15 skipped.

## License

**BLUX Proprietary License** (proprietary). Copyright (c) 2026 Outer-Void / Justadudeinspace (JADIS). All rights reserved. See [LICENSE](LICENSE).

Copies and releases obtained before 2026-10-25 remain available under the Apache License 2.0 they were
published with; every change and release from that date on is proprietary.

Contact: outervoid.blux@gmail.com (main) · theoutervoid@outlook.com (fallback)
