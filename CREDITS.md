# Credits: BLUX

Expanded, evidence-backed records: [Outer-Void/credits](https://github.com/Outer-Void/credits), record
`credits:repo:blux`. That ledger supplements, and does not replace, the notices in this repository.

## Third-party material
None is bundled. `blux-sdk` and `outervoid-blux` declare these runtime dependencies, which are installed
separately and keep their own licences:
- jsonschema, referencing and PyYAML (MIT).

Development tooling: pytest, ruff, mypy, build, setuptools, and GitHub Actions.

Phase 1 optional offline training tooling additionally installs scikit-learn, NumPy and SciPy
(BSD-3-Clause), joblib (BSD-3-Clause), and threadpoolctl (BSD-3-Clause) separately. None is bundled into
the runtime or model artifact. The portable classifier export uses numeric parameters and standard-library
inference; see [training/ARTIFACT-POLICY.md](training/ARTIFACT-POLICY.md).

Router experiment data is referenced, not copied, from Outer-Void/blux-coga-dataset and
Outer-Void/blux-ca-dataset under their BLUX Proprietary Licenses. Exact commits, original authorship and
engine provenance are recorded in [the experiment audit](training/experiments/AUDIT.md) and frozen
metadata. No external provider responses or new training examples were collected for this experiment.

## Standards implemented (independent implementations, no code copied)
- JSON Schema Draft 2020-12
- RFC 3339
- ULID specification
- Crockford Base32
- Semantic Versioning 2.0.0

`.gitignore` comes from the github/gitignore Python template (CC0-1.0).

## Influence and derived data
Hermes Agent (Nous Research) informed BLUX planning around agents and skills (`insp:hermes-agent-architecture`).
**No Hermes/BLHERMES code is in this repository.**

Since Stage 11, `migration/blhermes/` holds data derived from Hermes Agent: an inventory of upstream
identifiers and paths, and recorded outputs of upstream code. That data is used under the Hermes Agent MIT
License, which is reproduced in [migration/blhermes/UPSTREAM-NOTICE.md](migration/blhermes/UPSTREAM-NOTICE.md)
(Copyright (c) 2025 Nous Research; lineage `LIN-008`, fork lineage `LIN-001`). Any code migrated later must
carry the same notice.

## AI assistance
The Stage 00 baseline and the Stage 01 contracts, SDK, runtime, CLI, tests and docs were produced with
Claude Code (Anthropic, Claude Opus 5.5), recorded in `Co-authored-by` trailers (`AIL` entries in the
ledger). This credit implies no endorsement by Anthropic.

Phase 1 router training infrastructure, tests, experiment execution and documentation were developed
with OpenAI Codex assistance. This acknowledgement does not imply endorsement. The earlier Anthropic
credit above remains historical attribution; no Claude/Anthropic outputs were newly added as training
examples by this experiment.

## Licensing
Since 2026-10-25 this repository, including the SDK and runtime packages, is under the BLUX Proprietary License
(`LicenseRef-BLUX-Proprietary`, see `LICENSE`); the package metadata matches. This was the owner's decision
on `UNR-008`. Copies and releases obtained before 2026-10-25 keep Apache-2.0.

## Native routing program: Phase 2 and development research

Router 0.2 tooling, validators, tests, deterministic owner-template expansion, and development research
were implemented with OpenAI Codex assistance. No Codex-authored natural-language corpus was added to
model training. The owner explicitly chose existing eligible source records with coverage gaps recorded.
Source commits, inherited authorship/rights, generation methods and attestations are preserved under
`training/phase2/evidence/`; engine regression datasets remain unchanged. TF-IDF, logistic regression,
softmax/temperature scaling, character n-grams and grouped cross-validation are standard techniques,
implemented using the separately installed dependencies already listed above; no new library is bundled.
