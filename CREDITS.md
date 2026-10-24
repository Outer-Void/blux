# Credits: BLUX

Expanded, evidence-backed records: [Outer-Void/credits](https://github.com/Outer-Void/credits), record
`credits:repo:blux`. That ledger supplements, and does not replace, the notices in this repository.

## Third-party material
None is bundled. `blux-sdk` and `outervoid-blux` declare these runtime dependencies, which are installed
separately and keep their own licences:
- jsonschema, referencing and PyYAML (MIT).

Development tooling: pytest, ruff, mypy, build, setuptools, and GitHub Actions.

## Standards implemented (independent implementations, no code copied)
- JSON Schema Draft 2020-12
- RFC 3339
- ULID specification
- Crockford Base32
- Semantic Versioning 2.0.0

`.gitignore` comes from the github/gitignore Python template (CC0-1.0).

## Influence
Hermes Agent (Nous Research) informed BLUX planning around agents and skills (`insp:hermes-agent-architecture`).
**No Hermes/BLHERMES code is in this repository.** Any future migration is tracked as lineage `LIN-001` in
the credits ledger and must carry the upstream MIT notice.

## AI assistance
The Stage 00 baseline and the Stage 01 contracts, SDK, runtime, CLI, tests and docs were produced with
Claude Code (Anthropic, Claude Opus 5.5), recorded in `Co-authored-by` trailers (`AIL` entries in the
ledger). This credit implies no endorsement by Anthropic.

## Licensing
This repository's `LICENSE` is currently Apache-2.0, and the package metadata matches. Outer Void is
proprietary by default. Whether future BLUX versions are relicensed is an open owner decision (`UNR-008`),
and already-published versions keep Apache-2.0.
