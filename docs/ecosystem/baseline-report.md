# Stage 00: Organization-Wide Baseline

- **Baselined:** 2026-10-24 (UTC)
- **Scope:** every git repository under `~/ov` and `~/ov/ov-webpages` (30 repos)
- **Host:** aarch64 Debian under PRoot on an Android kernel. Python 3.13.5, Node 20.19.2, OpenJDK 21, shellcheck. **No Android SDK.** `gh` is not authenticated.

Machine-readable companions in this directory:
`repositories.json` · `dependency-graph.json` · `capabilities.json` · `migration-status.json` · `compatibility-matrix.json`

## Hard gate

**SATISFIED.** All 30 active repositories have a recorded baseline SHA, and every recorded SHA was checked against `git rev-parse HEAD`.

When the baseline was taken, every repository was:
- on `main`;
- clean (no uncommitted changes); and
- in sync with `origin/main` (0 ahead, 0 behind) after a safe fetch (`git fetch --all --prune --tags`, no merges).

No source changes were made to any repository. Test runs used throwaway virtualenvs and clean `git archive` exports in a scratch directory. Gitignored build output created by these runs (`build/`, `*.egg-info`, `htmlcov/`, `.coverage`, `artifacts/`) and one stray `roughin/backend/roughin.db` were removed afterwards.

## Method

| Area | How |
| --- | --- |
| Remote state | `git fetch --all --prune --tags`, then `rev-list --left-right --count HEAD...@{u}` |
| Visibility | Unauthenticated GitHub API: 200 means public. A 404 combined with a successful SSH fetch means private. |
| Python | Fresh venv per repo, non-editable `pip install`, `pytest -p no:cacheprovider` with `PYTHONDONTWRITEBYTECODE=1` |
| Node | `npm ci`, `eslint`, `tsc --noEmit`, `next build` / `vite build`, `npm audit --omit=dev`, all on a clean export |
| Kotlin/JVM | `./gradlew -Dorg.gradle.configureondemand=true :core:test` on a clean export. APK assembly is BLOCKED because there is no SDK. |
| Shell | `bash -n` plus `shellcheck -S error` and `-S warning` |
| Cross-repo contracts | Ran each dataset's verifier against its sibling engine checkout |
| Secrets | High-signal regexes (AWS, OpenAI, Anthropic, GitHub, Slack, Telegram, Google, Stripe, private keys) over tracked files, plus tracked `.env` files |

## Repo-by-repo summary

| Repo | Vis | Starting SHA | Test / build state | Architecture role | Integration status | Release state | Blockers |
|---|---|---|---|---|---|---|---|
| .github | pub | `6fe2eddb` | NOT PRESENT | Org profile + doctrine | NEEDS_REPAIR | n/a | Ecosystem diagram lists 5 repos that don't exist. No LICENSE. |
| .github-private | priv | `ba637913` | NOT PRESENT | Member profile | BASELINED | n/a | none |
| axiom | pub | `a0fe69e8` | tests FAIL 11/13; `bash -n` PASS; shellcheck 2 errors (ksh idiom) | Terminal cockpit (trifecta) | NEEDS_REPAIR | VERSION file, no tags | 2 failing tests, stale Justadudeinspace links, no CI |
| blhermes-agent | pub (fork) | `75624a7a` | NOT RUN (PM/uv bootstrap fails on this host). In-repo receipts at HEAD not re-verified. | Agent runtime (Hermes fork) | TRANSITIONAL | No fork release; services upstream | Nous upstream hooks; 4,257-file rebrand diff; device/APT gates NOT RUN |
| blux | pub | `42f4e5e3` | NOT PRESENT | Ecosystem coordination | BASELINED | Initial commit | none |
| blux-app | pub | `a361cc02` | pytest PASS (1 + 4 subtests) | Read-only artifact viewer | READY_FOR_INTEGRATION | Phase 1, no tags | Proprietary in public repo; no packaging or CI |
| blux-ca | pub | `8817f902` | pytest PASS 27; dataset live verify PASS | cA engine (cA-1.0-pro) | READY_FOR_INTEGRATION | Declares frozen 1.0.0; last tag `cA-0.1` | No 1.0.0 tag; proprietary in public repo; no CI |
| blux-ca-dataset | pub | `fbc31fba` | validate PASS; live verify PASS | cA fixtures / HF export | READY_FOR_INTEGRATION | DATASET_VERSION, no tags | Stale `codex/*` branches; no CI |
| blux-coga | pub | `a7d88a25` | pytest PASS 32; harness PASS | CogA engine (CogA-1.0-pro) | READY_FOR_INTEGRATION | Declares frozen 1.0.0; last tag `CogA-0.1-mini` | No 1.0.0 tag; no CI |
| blux-coga-dataset | pub | `b3abf26b` | verify PASS; harness PASS | CogA drift fixtures | READY_FOR_INTEGRATION | no tags | No CI |
| blux-gpt | pub | `cbb08831` | pytest PASS 26 | Multi-provider advisory CLI | BASELINED | "v1.0 lock", no tags | Hardcoded model IDs not verified; no CI |
| blux-system | pub | `db70f0fd` | pytest FAIL installed (5/14); PASS from source (14) | State / receipt contracts | NEEDS_REPAIR | no tags | `schemas/` not packaged |
| blux10k | pub | `05894b82` | `bash -n` / shellcheck PASS; no tests | Dotfiles (trifecta) | BASELINED | v2.0, no tags | No LICENSE |
| dat | pub | `5fb9de9b` | pytest FAIL (53 pass, 3 fail with fixes applied) | Audit engine (used by lrc) | NEEDS_REPAIR | pyproject 3.0.0 vs tag `v2.0.0-Stable` | CLI/test drift; no asyncio config; duplicate release workflow |
| docs | pub | `a604ab04` | NOT PRESENT | Legal / policy | BASELINED | n/a | Covers DevBoard and Grit & Grain, which have no repos |
| god | pub | `699c1d02` | pytest FAIL (7 pass, 1 fail: stale version assertion) | Help indexer CLI | NEEDS_REPAIR | **PyPI god-cli 1.0.1**; git tag v1.0.0 | Stale test; no v1.0.1 tag |
| jadis.toolbox | pub | `baf97c06` | `bash -n` / shellcheck PASS | Operator checklists | BASELINED | n/a | none |
| lrc | pub | `4dbeb99c` | pytest FAIL (2 collection errors) | Repo compiler | NEEDS_REPAIR | 1.0.0-alpha.1 vs tag `v1.0.0-stable` | Module/package shadowing; PyPI name `lrc` taken; duplicate workflow |
| resolver | priv | `65c2bf03` | pytest PASS 173 | TheResolverBot Telegram platform | RELEASE_CANDIDATE | `v4.0` is 71 commits behind | Untagged RC; confirm the second bot handle |
| roughin | pub | `d4f2fdbc` | backend FAIL (missing `greenlet`), PASS 5 once added; frontend build PASS | Contractor SaaS | NEEDS_REPAIR | no tags | Missing dep; DB not ignored; "confidential" code in public repo |
| termux-background | pub | `efa3b3c9` | Android BLOCKED; shell PASS | Termux:Monet wallpaper companion | NEEDS_REPAIR | README: "v1.0.0 Not Operational" | Device qualification outstanding |
| vigyl | pub | `45b29709` | NOT PRESENT (0 tests) | Caregiving system (Stage 1) | BASELINED | Stage 1 | Privacy-sensitive proprietary code in public repo |
| void.game | pub | `ffb333ae` | `:core:test` compile PASS (no tests); APK BLOCKED | Axiom Shooter | TRANSITIONAL | v2.0.0-dev | Diverged copy lives in void.os |
| void.os | pub | `bd871e2a` | APK/tests BLOCKED (no SDK) | AXIOM Launcher | TRANSITIONAL | v2.0.0-dev | Vendored game; duplicated icons; 7 local-only merged branches |
| void.sys | pub | `9765f8f4` | APK BLOCKED; no tests | Icon system / pipeline | BASELINED | no tags | Paid asset source public; manual export to void.os |
| voidforge | pub | `6a1f4d3d` | `:core:test` PASS; APK BLOCKED | ADB/Fastboot workstation | BASELINED | Dev preview | Device writes locked by design |
| bluxai.app | priv | `a3376fda` | NOT PRESENT | Placeholder site | BASELINED | Initial commit | Empty |
| ivrp | priv | `dfaae26c` | NOT PRESENT | Placeholder site | BASELINED | README only | Empty; no LICENSE |
| operationrestore | priv | `17a0d41f` | HTML parse + link check PASS | operationrestore.net static site | BASELINED | v1 static | No LICENSE |
| theresolver | priv | `e4847761` | build PASS; tsc PASS; eslint FAIL (65); audit FAIL (1 critical) | TheResolverBot ad site | NEEDS_REPAIR | no tags | Bump next to ≥16.4.3; lint errors |

Status counts: 12 BASELINED · 9 NEEDS_REPAIR · 5 READY_FOR_INTEGRATION · 3 TRANSITIONAL · 1 RELEASE_CANDIDATE · 0 DEPRECATED.

## Findings

### Broken or incomplete functionality
- **blux-system:** `core.py` finds `schemas/` relative to the installed package. The wheel doesn't ship them, so an installed `blux-system` can't validate receipts. Tests fail 5/14 installed and pass 14/14 from source.
- **lrc:** `compiler.py`, `parser.py` and `generator.py` sit beside packages of the same name. The packages shadow the modules, and tests importing `IS_WINDOWS` / `compile_schema_path` fail at collection.
- **dat:**
  - The three `tests/test_cli.py` failures call subcommands the CLI no longer has.
  - Async tests need `asyncio_mode`, which isn't configured.
  - `reportlab` (the `pdf` extra) is required for test collection.
- **god:** `test_version_flag` expects v1.0.0 but the package (and PyPI) is 1.0.1.
- **roughin:** `requirements.txt` is missing `greenlet`, which SQLAlchemy asyncio needs. Test runs create `backend/roughin.db`, and nothing gitignores it.
- **axiom:** 2 of 13 shell tests fail (`log.sh` logging, `axiom.sh` dry-run). These ran in an isolated copy and may be environment-sensitive.
- **termux-background:** self-declared not operational.
- **theresolver:** `eslint` reports 65 errors, all `react/no-unescaped-entities`.

### Dead code
- lrc: the shadowed `compiler.py`, `parser.py`, `generator.py`.
- theresolver: `functions/_middleware.ts` is a no-op pass-through.
- void.os: 7 local-only branches, all merged with 0 unique commits, so they're safe to prune.
- blux-ca-dataset: stale remote `codex/*` branches.
- termux-background: a stale remote branch, `Justadudeinspace-patch-1`.

### Duplicated responsibility
- **Axiom Shooter** source exists in both void.game and void.os, and the copies have diverged (22 of 27 Kotlin files differ). There is no declared source of truth.
- **VOID.SYS PNGs** are fully duplicated in void.os (620/620 identical) with no sync tool on the void.os side.
- **LLM provider access** is implemented three separate times: blhermes-agent, blux-gpt and resolver each have their own keys, model selection and providers.
- **Release workflows:** dat and lrc each contain both `sign-and-release.yml` and `sign-and-release.yaml`.
- **Not a duplicate:** `resolver` (bot runtime) and `ov-webpages/theresolver` (advertising site) are distinct, by rule.

### Stale branding and metadata
- `.github/docs/ecosystem-diagram.md` describes `blux-core`, `blux-orbit`, `blux-design-system`, `blux-experience-kit` and `blux-governance`. None of them exist.
- Old-owner `github.com/Justadudeinspace/` links remain:

  | Repo | Links |
  | --- | --- |
  | dat | 16 |
  | lrc | 7 |
  | axiom | 3 |
  | termux-background | 3 |
  | voidforge | 2 |
  | jadis.toolbox | 1 |

  `BLHERMES_BASELINE.md` also still cites `Justadudeinspace/blhermes-agent` as origin.
- Contact email is split. `outervoid.blux@gmail.com` is used by the blux-* repos, dat and god. `theoutervoid@outlook.com` is used by roughin, void.*, vigyl, axiom and lrc.
- The declared release identity doesn't match the tags:

  | Repo | Declared | Latest tag |
  | --- | --- | --- |
  | blux-ca | 1.0.0 | cA-0.1 |
  | blux-coga | 1.0.0 | CogA-0.1-mini |
  | dat | 3.0.0 | v2.0.0-Stable |
  | lrc | 1.0.0-alpha.1 | v1.0.0-stable |
  | god | 1.0.1 | v1.0.0 |
  | resolver | HEAD | v4.0 (71 commits behind) |

- god's README claims "BLUX integration", but no code coupling to BLUX exists.

### Hermes / Nous / upstream hooks (blhermes-agent)
- **Updates and releases:** `hermes_cli/update_cmd_git.py` and `source_releases.py` (`OFFICIAL_REPOSITORY = "NousResearch/hermes-agent"`) target upstream, and `banner.py` release links do too.
- **Remote catalogs:** `model_catalog.py` and `local_runtime/catalog.py` fetch from `raw.githubusercontent.com/NousResearch/hermes-agent`. Plugin catalogs point at `NousResearch/hermes-official-plugins`, `hermes-labs-plugins` and `hermes-nvidia`.
- **Installers:** `hermes-agent.nousresearch.com/install.sh|.ps1`.
- **Services:** the default sandbox image is `nousresearch/hermes-sandbox:desktop`, and there are Nous Portal, billing, device and inference-API endpoints.
- **Telemetry:** the endpoint is `telemetry.nousresearch.com`, but it is **opt-in**. Both `enabled` and `send` default to `False`.
- **Merge cost:** the fork is only 14 commits past upstream base `3fa571a5`, but those commits touch 4,257 files (a textual rebrand). Upstream main has moved to `5894e750`, past the recorded `55ee4acd`.

### Undocumented cross-repo coupling
- void.os ↔ void.game (vendored source) and void.os ← void.sys (assets). Neither sync is documented.
- theresolver hardcodes the resolver bot handles and `admin.theresolverbot.net`, so a handle change in the bot must be mirrored by hand. The bot code uses two public bot identities (`@TheResolverBot` and `t.me/ResolvingBot`). Confirm that's intended.
- blux-app consumes engine JSON by filename. It renders version-negotiation fields but doesn't validate them against blux-ca, blux-coga or blux-system schemas.
- **Documented** couplings, for contrast:
  - lrc → `dat audit` (CLI);
  - dataset → engine (env vars `BLUX_CA_ENGINE_ROOT`, `BLUX_COGA_REPO`);
  - void.game → AXIOM signing-cert verification;
  - the axiom / blux10k / blux-gpt trifecta.
- No repo depends on another through a package manager. All coupling is CLI, file contract, vendored copy or hardcoded content.

### Security boundaries
- **No committed secrets.** The single hit is a fake `xoxb-wire…` placeholder in a blhermes eval. No tracked `.env` files were found. resolver ignores `.env*` and cloudflared credentials, and Android signing reads from variables.
- **theresolver:** `next@16.1.6` has a critical advisory, plus 4 high (postcss, sharp, nanoid, source-map-js). Fix with `next@16.4.3` (non-major). It's a static export, so server-side impact is limited.
- **blhermes-agent:** its own review says plugins load in-process with the agent's credentials, and YOLO mode bypasses approval. Its rule, recorded here as an ecosystem constraint: VoidForge device-destructive operations must sit behind a separately authenticated authority, never behind agent approval.

### Public/private boundary problems
Nine **public** repos carry proprietary or "all rights reserved" licenses:

| Repo(s) | Problem |
| --- | --- |
| blux-app, blux-ca, blux-ca-dataset, blux-coga, blux-coga-dataset, blux-system | BLUX Proprietary License |
| roughin | Header says "proprietary and confidential" |
| vigyl | Privacy-sensitive caregiving product |
| void.os, void.game, void.sys | Paid products: $4.99 launcher, $1.99 icon pack |

Licensing a public repo restricts use but doesn't keep the source private. Each of these needs a deliberate decision: make it private, or accept that the source is public. Separately, five repos have no LICENSE at all: `.github`, blux10k, ivrp, operationrestore and theresolver.

### Release blockers (cross-cutting)
1. **No CI** in 22 of 30 repos, including all six BLUX contract repos. Only these have workflows: blhermes-agent, dat, god, lrc, resolver, termux-background, void.os and voidforge.
2. **No tags** for declared frozen versions (blux-ca, blux-coga) or for the resolver release candidate.
3. **The lrc PyPI name is already taken** (`lrc` 0.1.5 belongs to an unrelated project).
4. **No Android build** can be verified on this host. Android repos need an SDK-equipped runner or device lane.
5. **blhermes-agent** production, APT and device acceptance is NOT RUN.

## blux-web

**Not found. Nothing to preserve.**

- Absent locally under `~/` (searched to depth 4).
- Outer-Void: GitHub API returns 404, it isn't in the 24-repo public org listing, and SSH ls-remote says "Repository not found", using the same key that reads the org's private repos.
- `Justadudeinspace/blux-web`: 404 on the API and not found over SSH.
- No tracked file or commit message in any of the 30 repos mentions `blux-web`.

If it was deleted or lives under another owner, recovery needs GitHub org audit-log access (`gh auth login`).

## Not run / limits
- Android `assemble`, `lint` and instrumented tests: BLOCKED, because no Android SDK is installed.
- The full blhermes-agent suites: NOT RUN. The in-repo receipts are cited but weren't re-verified.
- No GitHub Actions run history was checked, because `gh` is unauthenticated, so CI state is unknown for every repo.
- blux-gpt model IDs weren't verified against provider catalogs.
