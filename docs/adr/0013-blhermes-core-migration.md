# ADR-0013: BLHermes core migrated into BLUX; temporary compatibility policy

- Status: Accepted (Stage 13, 2026-10-26)

## Context
Stage 11 inventoried BLHermes and recorded characterization goldens for its retained core behaviour.
Stage 13 moves that core into BLUX-owned modules in a fixed order:
1. config and state;
2. `BLUX_HOME` and the `HERMES_HOME` reader;
3. providers;
4. tool execution;
5. approvals;
6. credentials;
7. sessions.

## Decision
1. **Clean-room, golden-driven.** Each slice is a BLUX implementation tested against the Stage 11 goldens
   (`tests/core/test_core_migration.py`). No upstream code is copied. Provenance stays in credits
   (`LIN-001`, `LIN-008`), and the goldens' MIT notice is in `migration/blhermes/UPSTREAM-NOTICE.md`.
2. **BLUX names at public boundaries.**

   | Slice | BLUX interface | Golden |
   |---|---|---|
   | state | `BLUX_HOME`, `BLUX_PROFILE`, `blux_runtime.state` (profiles under `profiles/<name>`, root = `default`) | paths |
   | providers | Model API + BluxGPT routes (Stage 09); legacy keys map to BluxGPT names | provider_registry |
   | tools | `shell.exec`, `fs.read`, `fs.write` capabilities (`labels.kind = tool`) | tool_registry (partial) |
   | approvals | `blux_runtime.security.detect`, `SessionApprovals` | approval_detection, approval_session_state |
   | file safety | `blux_runtime.security.paths` | file_safety |
   | credentials | allow-list `child_env`, `redact`, BluxGPT-owned keys | child_env_scrub, redaction |
   | sessions | `blux_runtime.sessions.SessionStore` (emits `session.*`) | session_store |

3. **Stricter where BLHermes was weak.** Each difference is listed in the tests:
   - **Dangerous commands.** `shutdown`/`reboot`, killing init, and bootloader lock changes count as
     dangerous.
   - **Device commands.** Device commands (fastboot, bootloader, partition, destructive adb) need a
     one-time approval per command. Session approvals and YOLO never cover them.
   - **Write safety.** Legacy `auth.json` is write-denied. Shell start-up files and `.gitconfig` need
     approval. Since OV-50, so does a write into a workspace's `.git/` (hooks, config) or `.blux/`, which hold
     code that runs later.
   - **Shell matches the file tools (OV-50).** A command that mentions a credential file fs.read refuses
     (`credential.path`), or writes a start-up file, a PATH directory, a git hook or `.blux/` (`autorun.write`),
     needs approval. Command detection is still pattern matching, not a sandbox: it narrows what runs without
     approval but cannot prove that a command stays inside the workspace.
   - **Every shell command needs approval (OV-62, F95, owner decision 2026-11-09).** A drill showed five of nine
     escape attempts writing or deleting outside the workspace with no approval (`../`, absolute paths, shell
     variables, an obfuscated `rm`). Until an OS sandbox exists (none works on Android/PRoot), `shell.exec`
     runs a command only with a one-time approval of that exact command, or with YOLO, which only the person can
     switch on for a session. Session approvals of a rule no longer cover shell commands. Detection still
     names the rule, so the person sees why a command is risky.
   - **Output and cancellation (OV-50).** `shell.exec` keeps at most 64 KiB per stream while it reads, and a
     cancelled call kills the command's process group instead of letting it finish.
   - **Child environment.** It is an allow-list, so `AWS_SECRET_ACCESS_KEY`, `DATABASE_URL` and other
     secrets never reach a child. BLHermes leaked them.
4. **Temporary compatibility is explicit and time-bounded.**
   - **The only path.** `blux_runtime.compat.REGISTRY` lists every compatibility path. Today it holds one,
     `hermes-home-import`, which expires on 2027-04-30.
   - **On request only.** It runs only on request (`blux migrate hermes`). It is a dry run unless
     `--apply`, and it reads the legacy home without modifying it.
   - **Expiry.** After expiry it refuses to run, and the test suite fails until it is removed.
   - **Sessions.** It imports sessions and their explicit messages only. Hidden reasoning columns are
     never read.
   - **Keys.** Provider keys move to BluxGPT's config only with `--credentials` (0600, existing values
     kept). Values are never printed.
   - **No permanent fallback.** BLUX never uses `HERMES_HOME` as its own state.
5. **Obsolete upstream hooks are not migrated.** The migrated slices contain no Nous Portal, update-feed or
   telemetry hooks (enforced by `tests/migration/test_blhermes_matrix.py`). Upstream hook names are not
   reused; BLUX uses its Hook System v1 events.
6. **Events and receipts.**
   - **Tools.** Tools run through the runtime, so they get the `tool.*`/`capability.*` hook events, policy
     and receipts.
   - **Sessions.** Sessions emit `session.created`, `session.loaded` and `session.closed`.
7. **Evidence, not copying.** In the parity matrix, IN_PROGRESS and MIGRATED features must cite existing
   BLUX tests (`evidence`). The matrix test rejects anything else.

## Consequences
- **Hard gate.** The migrated core runs from `/blux` without importing the fork; a subprocess test checks
  that no fork module or path is ever loaded.
- **What stays open.** These remain IN_PROGRESS with named blockers: the other providers, the other tools,
  and session branching, rewind and compression.
