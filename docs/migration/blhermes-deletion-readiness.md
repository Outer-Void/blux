# BLHermes deletion-readiness report (Stage 16)

- Date: 2026-10-26
- BLUX: branch `stage-16/decoupling` → `main`
- Fork: `Outer-Void/blhermes-agent` at `ac51a44f1f73452446ddea8e51c58e9e5e9098fc`. It is untouched; nothing was
  deleted.
- Evidence: [migration/blhermes/decoupling-evidence.json](../../migration/blhermes/decoupling-evidence.json).
  The decoupling scan is [migration/blhermes/decoupling.py](../../migration/blhermes/decoupling.py), enforced
  in CI by `tests/migration/test_blhermes_decoupling.py`.

## Verdict
**BLUX does not depend on the fork. Removing it would not break BLUX's build, tests, packaging, runtime,
docs or CI.** This was proven by doing it: a fresh clone of BLUX and BluxGPT in an isolated directory, with
no `blhermes-agent` anywhere beside it, built and passed everything (table below).

**Recommendation for Stage 17: archive the fork, do not delete it.** BLUX doesn't need it, but 20 retained
features (15 NOT_STARTED, 5 DEFERRED) still have the fork as their only reference source and
characterization target. The upstream provenance also has archival value.

## Proof with the fork absent
| Check | Result |
|---|---|
| Schema lock, format, lint, types | PASS |
| Test suite | **543 passed, 0 failed**, 20 skipped. The skips are 16 fork-dependent reference checks (characterization, matrix locations, no-deletion, rescan, optional SKILL.md format), which skip cleanly, and 2 real-provider tests. CogA/cA integration re-run with project dirs: 9 passed |
| Wheel round trip | PASS (contracts, Model API, Hook System) |
| Termux `.deb` build and artifact audit | PASS |
| Decoupling scan (every tracked file) | 0 violations |
| DAT release audit (`dat release --gate`) | **pass**: 0 blocking, 14 exact-fingerprint suppressions of deliberately fake test credentials (`.dat/policy.json`). Remaining: medium `release.untagged-version` (no release tag; tagging is the owner's call), and two info findings |

## Searches (decoupling scan)
Each class is matched over every tracked file. A hit is allowed only by an explicit rule with a reason
(`ALLOWED` in the scanner).

| Class | Violations | Allowed hits and why |
|---|---|---|
| Source imports of fork modules | 0 | only `migration/blhermes/characterization/probe.py`, which imports the fork from a separate checkout at test time |
| Build dependencies | 0 | only the probe interpreter's pins (`characterization/requirements.txt`) |
| CI fetches | 0 | none |
| Packaging dependencies | 0 | the package notice says no BLHermes code ships; the postinst hint for legacy state |
| Hermes/Nous product URLs | 0 | inventory, goldens and matrix data; historical baseline docs |
| Updater or feed hooks | 0 | inventory data and scanner patterns only |
| Telemetry or network hooks | 0 | inventory data and scanner patterns only |
| `HERMES_HOME` hard requirements | 0 | the time-bounded importer (opt-in, expires 2027-04-30), the credential-path deny list, synthetic legacy tests. BLUX never requires `HERMES_HOME` |
| Hermes command or package identity | 0 | inventory data only. Entry points are `blux` and `bluxgpt`; the package is `blux` |
| Stale branding | 0 | provenance notes in module docstrings, migration docs and history, and DAT suppression reasons |

## Confirmations
| Gate | Status | Evidence |
|---|---|---|
| Provenance and licence obligations | **Preserved and reconciled** | **In BLUX.** `migration/blhermes/UPSTREAM-NOTICE.md` reproduces the Hermes Agent MIT licence for the derived data; `CREDITS.md`. **Credits ledger.** `LIN-001` (fork, file and subsystem lineage), `LIN-008` (derived data), `ATT-249`, `proj:hermes-agent`. **Package.** The `.deb` ships no Hermes-derived data or code. |
| BLUX Hook System | **Complete (v1)** | 37 events in 13 families; contract `contracts/hooks/v1`; `tests/hooks` (ADR-0012) |
| Provider/Model API | **API complete; provider breadth reduced** | Model API v1 with logical models (ADR-0010); BluxGPT routes OpenAI, Anthropic, DeepSeek and OpenAI-compatible servers. Other upstream providers are BLH-002 IN_PROGRESS |
| State migration | **Passes** | `blux migrate hermes`: dry run, apply, idempotent, no hidden reasoning, legacy home untouched (`tests/core`, packaging validator) |
| Packaging | **BLUX-owned** | `packaging/termux` (ADR-0015); BLHermes PM, installer and updater superseded |
| Runtime parity matrix | **Complete as a record; parity partial** | All 44 features carry a state, target, tests or evidence plan, and blocker. MIGRATED 8, IN_PROGRESS 10, CHARACTERIZED 2, NOT_STARTED 15, DEFERRED 5, PLANNED_REMOVAL 3, KEPT_AS_ATTRIBUTION 1 |
| DAT release audit | **Pass** | See the proof table |
| Termux/Android acceptance | **Explicit** | Bionic runtime PASS (Termux python 3.14.8 on a real Android kernel). Device installation through Termux APT is **BLOCKED** (owner steps in `packaging/termux/ACCEPTANCE.md`) |

## What is lost if the fork is deleted (not archived)
- **Reference source.** It goes for the features not yet migrated:
  - memory (BLH-013);
  - MCP server and optional MCP catalogue (BLH-018/019);
  - messaging platform adapters (BLH-022);
  - ACP (BLH-029);
  - platform detection (BLH-032);
  - browser and computer use (BLH-037);
  - the desktop app, web dashboard, LSP, locales and native helpers (DEFERRED);
  - the upstream test suites (BLH-040).
- **Re-runnable characterization.** The 12 goldens stay in BLUX, but they could no longer be re-recorded.
- **File-level upstream history.** It remains on GitHub upstream (NousResearch/hermes-agent) and in the
  credits lineage.

## Not done (by instruction)
The fork was not deleted, archived or modified. Stage 17 is a human decision: review this report, then
approve the retirement operation.
