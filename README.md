# BLUX

BLUX is the contract authority for the Outer-Void ecosystem. It defines the canonical contracts, SDKs, capability
registry, runtime and model interfaces, and the `blux` command line. Other repositories stay independent and
integrate through a `.blux/` directory (federated, not a monorepo; see ADR-0001).

**Status:** Stage 01. Contract v1 (`1.0`), Python SDK `blux-sdk 0.1.0`, reference runtime + CLI `outervoid-blux 0.1.0`.

## Layout
| Path | Contents |
|---|---|
| `contracts/v1/` | Canonical JSON Schemas (locked by `CHECKSUMS.sha256`) |
| `sdk/python/` | `blux-sdk`: validation, IDs, envelopes, errors, serialization, version negotiation |
| `runtime/` | `blux_runtime`: capability registry, policy engine, executor, built-in `echo.test` |
| `cli/` | `blux_cli`: the `blux` command (one JSON document per invocation on stdout) |
| `intelligence/` | Scope of the direct BLUX intelligence surface (no providers yet) |
| `registry/` | Federated capability index |
| `integrations/` | How repositories integrate |
| `training/` | Reserved |
| `packaging/` | Schema sync/lock, wheel verification |
| `tests/` | Contract, SDK, runtime, compatibility and CLI tests, plus v1 fixtures |
| `docs/` | ADRs, contract reference, `.blux/` spec, versioning, ecosystem baseline |
| `.blux/` | This repository's own BLUX manifest |

## Quick start
```sh
make install            # editable sdk + runtime/cli + dev tools
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
[0008](docs/adr/0008-no-arbitrary-stdout-protocol.md) no arbitrary stdout

## Out of scope for Stage 01
BLHermes is not absorbed. There are no web/UI layers. There is no receipt persistence, because blux-system owns it.
The PyPI name `blux` belongs to an unrelated project, so the runtime distribution is `outervoid-blux`.
