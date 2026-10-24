# ADR-0005: Version negotiation

- Status: Accepted (Stage 01, 2026-10-24)

## Decision
`contract_version` is a **string** `MAJOR.MINOR`. A YAML `1.0` (a float) or `"1.0.0"` is malformed.

Two different failures, two different codes:

| Input | Code |
|---|---|
| Not `MAJOR.MINOR` (number, `v1`, `1.0.0`, missing value) | `MALFORMED_CONTRACT_VERSION` |
| Well-formed but not in the reader's supported set (`2.0`, `1.1` today, `0.9`) | `UNSUPPORTED_CONTRACT_VERSION`, with `details.supported` |

- A reader accepts **exactly** its supported set (`SUPPORTED_CONTRACT_VERSIONS = ("1.0",)`). A newer minor is rejected, not guessed at, because v1 schemas forbid unknown fields.
- `negotiate(offered, supported)` picks the highest common version, comparing numerically, so `1.10 > 1.9`.
- The runtime also requires the request's version to be in the capability's `contract_versions`.

### Changing the contract
- **Minor (1.x)**: additive and optional only. Add schemas and fixtures, extend the supported set, keep every frozen 1.0 fixture valid, and update `CHANGELOG.md`.
- **Major**: a new `contracts/v2/` tree alongside v1. Never edit v1 in place.
- `contracts/v1/CHECKSUMS.sha256` locks the schemas. CI fails on any unreviewed edit. After review, run `python packaging/sync_schemas.py && python packaging/sync_schemas.py --write-lock`.
