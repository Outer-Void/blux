# Ecosystem licensing (Stage 01A)

> **Outer Void is proprietary by default. Third-party rights remain third-party rights. Attribution is
> preserved beyond the minimum legal requirement where provenance is known.**

The source of truth is the credits ledger ([Outer-Void/credits](https://github.com/Outer-Void/credits),
`projects/*.yaml`). `repositories.json` mirrors each repository's `licensing` block (decision, current
licence, traveling notices, unresolved items).

| Decision | Repositories |
|---|---|
| UPSTREAM_LICENSE_RETAINED | blhermes-agent (MIT, Nous Research) |
| MIXED_WITH_THIRD_PARTY_NOTICES | credits, void.os, void.game, void.sys, roughin, blux10k, theresolver, termux-background, voidforge |
| PUBLIC_SOURCE_PROPRIETARY | .github, axiom, blux, blux-app, blux-ca, blux-ca-dataset, blux-coga, blux-coga-dataset, blux-gpt, blux-system, dat, docs, god, jadis.toolbox, lrc, vigyl |
| PRIVATE_PROPRIETARY | .github-private, resolver, ivrp, bluxai.app |
| REVIEW_REQUIRED | operationrestore (ownership `UNR-006`) |

Since 2026-10-25 (owner decision, `UNR-008` resolved) every Outer Void-owned repository carries a
proprietary licence: the BLUX Proprietary License (`LicenseRef-BLUX-Proprietary`) for BLUX repositories, or
the Outer-Void Proprietary License (`LicenseRef-OuterVoid-Proprietary`) or a product-named equivalent
elsewhere. Copyright (c) 2026 Outer-Void / Justadudeinspace (JADIS). Contact: outervoid.blux@gmail.com
(main), theoutervoid@outlook.com (fallback). Copies and releases published earlier under Apache-2.0, MIT or
CC BY 4.0 keep that licence.

## Rules for BLUX contract and integration work
- An integration must not copy third-party code into BLUX without a lineage record and its traveling notice.
- BLHERMES material entering BLUX (Stage 11) keeps the MIT notice of Nous Research (`LIN-001`). BLHERMES
  cannot be retired (Stage 17) until that notice and its lineage survive independently.
- Capability descriptors and `.blux/` manifests carry no licence claims. Licensing lives in the credits ledger
  and each repository's notice files.

Human actions pending: see the credits `reports/unresolved-provenance.md`, especially `UNR-002` (fonts),
`UNR-004` (trademarks), `UNR-006` (operationrestore ownership) and `UNR-014` (public visibility of proprietary repositories).
