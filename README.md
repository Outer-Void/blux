# BLUX

BLUX is the contract authority for the Outer-Void ecosystem. It defines the canonical contracts, SDKs, capability
registry, runtime and model interfaces, and the `blux` command line. Other repositories stay independent and
integrate through a `.blux/` directory (federated, not a monorepo; see ADR-0001).

**Status:** Stage 13 (BLHermes core migrated: state, tools, approvals, credentials, sessions). Hook System v1 (`1.0`); runtime + CLI `outervoid-blux 0.4.0` (SDK `blux-sdk 0.2.0` unchanged). Contract v1 (`1.0`) and BLUX Model API v1 (`1.0`), Python SDK `blux-sdk 0.2.0`, reference runtime + CLI `outervoid-blux 0.3.0`.

`blux` is direct intelligence; ecosystem orchestration is `ax`.

## Layout
| Path | Contents |
|---|---|
| `contracts/v1/` | Canonical JSON Schemas (locked by `CHECKSUMS.sha256`) |
| `contracts/model-api/v1/`, `contracts/hooks/v1/` | Model API v1 and Hook System v1 contracts (each with its own lock) |
| `sdk/python/` | `blux-sdk`: validation, IDs, envelopes, errors, serialization, version negotiation |
| `runtime/` | `blux_runtime`: capability registry, policy engine, executor, model router, hook bus (`blux_runtime.hooks`), built-in `echo.test` |
| `cli/` | `blux_cli`: the `blux` command: direct intelligence (`blux "..."`, chat, doctor; [docs/cli.md](docs/cli.md)) and the Contract v1 machine commands |
| `intelligence/` | Scope of the direct BLUX intelligence surface |
| `registry/` | Federated capability index |
| `integrations/` | How repositories integrate |
| `migration/blhermes/` | Stage 11 BLHermes forensic inventory, parity matrix and characterization goldens (MIT-derived data, see its UPSTREAM-NOTICE) |
| `training/` | Reserved |
| `packaging/` | Schema sync/lock, wheel verification |
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
[0013](docs/adr/0013-blhermes-core-migration.md) BLHermes core migration

## Licensing and credits
See [CREDITS.md](CREDITS.md) and [docs/ecosystem/licensing.md](docs/ecosystem/licensing.md).
Outer Void is proprietary by default. Third-party rights remain third-party rights.

## Out of scope for Stage 01
BLHermes is not absorbed. There are no web/UI layers. There is no receipt persistence, because blux-system owns it.
The PyPI name `blux` belongs to an unrelated project, so the runtime distribution is `outervoid-blux`.

## License

**BLUX Proprietary License** (proprietary). Copyright (c) 2026 Outer-Void / Justadudeinspace (JADIS). All rights reserved. See [LICENSE](LICENSE).

Copies and releases obtained before 2026-10-25 remain available under the Apache License 2.0 they were
published with; every change and release from that date on is proprietary.

Contact: outervoid.blux@gmail.com (main) · theoutervoid@outlook.com (fallback)
