# ADR-0001: Federated architecture, not a monorepo

- Status: Accepted (Stage 01, 2026-10-24)

## Context
The Stage 00 baseline (`docs/ecosystem/`) found 30 independent repositories. No repository depends on another
through a package manager. All coupling is CLI calls, file contracts, vendored copies or hardcoded content, and some
of it is undocumented and diverged (void.os ↔ void.game). Folding everything into one repository would merge
unrelated release cycles, licenses and visibility levels.

## Decision
BLUX is the **contract authority** of a federation. This repository owns:
- canonical contracts (`contracts/v1`);
- SDKs (`sdk/python`, first);
- the capability registry index (`registry/`);
- runtime and model interfaces (`runtime/`);
- the direct `blux` surface (`cli/`);
- integration metadata and specifications (`docs/spec`, `integrations/`).

Every other repository stays independent. A repository participates by declaring a `.blux/` directory and
exposing capabilities that satisfy Contract v1.

## Consequences
- Integration is contract-first. Cross-repo coupling becomes declared, versioned and testable.
- BLUX never vendors another repository's source. Product code is not copied into this repo.
- Repos keep their own CI, licensing and visibility.
