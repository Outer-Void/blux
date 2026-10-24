# ADR-0002: Standalone vs integrated boundary

- Status: Accepted (Stage 01, 2026-10-24)

## Decision
A project's `.blux/manifest.yaml` declares `role`:

| role | Meaning |
|---|---|
| `standalone` | Fully usable without BLUX. BLUX integration is an optional adapter, and removing `.blux/` must not break the product. |
| `integrated` | Requires a BLUX runtime to be useful (BLUX itself, future agents/orchestrators). |

Rules:
1. A standalone repo must never import BLUX runtime code on its main path. It may depend on `blux-sdk` only inside its integration adapter.
2. BLUX must never reach into a repository except through declared capabilities.
3. **Persistence authority stays with blux-system.** BLUX *emits* receipts to a `ReceiptSink` and keeps none durably. The reference `InMemoryReceiptSink` is for tests and the CLI. No database, store or replay log lives here.
4. No speculative web/UI layer. `blux-app` remains the viewer.

## Consequences
The runtime exposes a `ReceiptSink` protocol. A blux-system adapter is a later integration stage, not a reimplementation here.
