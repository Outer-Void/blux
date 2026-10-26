# ADR-0014: BLHermes extensions migrated onto BLUX capabilities and hooks

- Status: Accepted (Stage 14, 2026-10-26)

## Context
BLHermes extensions held upstream hook authority:
- in-process plugins with access to credentials;
- skills loaded without licence checks;
- MCP servers launched with the agent's environment;
- cron jobs and gateways with their own authorization paths.

Stage 14 moves the retained extension features onto BLUX's capability, policy and Hook System contracts.

## Decision
| Feature | BLUX module | Rules |
|---|---|---|
| skills | `extensions/skills.py` | **Format.** BLHermes SKILL.md is read as-is, plus `metadata.blux` (capabilities, hooks). **Admission.** A skill without a licence is rejected. **Load time.** No code runs at load. **Delivery.** Instructions are delivered secret-scrubbed through `skill.list` and `skill.get`. |
| plugins | `extensions/plugins.py` | **Manifest.** `blux-plugin.json`. **Capabilities.** Subprocess only (ADR-0009) and namespaced. **Hooks.** Only through `ExternalSubscribers` with operator grants. **Admission.** A licence is required. |
| MCP | `extensions/mcp.py` | **Tools.** Each stdio server tool becomes `mcp.<server>.<tool>`, which policy must grant via `mcp:<server>`. **Environment.** The allow-list plus the declared `credential_env` (announced as `credential.requested`). **Reliability.** Per-call timeout, with kill-and-restart. **Output.** Redacted. |
| agents and subagents | `extensions/agents.py` | **Loop.** Model and tool calls each go through the runtime (policy, approvals, hooks, receipts). **Undeclared tools.** The router rejects them. **Subagents.** A child only gets a subset of its parent's tools. Depth is bounded. **Events.** `agent.*`. |
| scheduler | `extensions/scheduler.py` | **Parsing.** Matches the cron golden. **Runs.** Jobs run as actor `service:scheduler` without session approvals, so dangerous commands are refused. **Isolation.** A failing job never stops the others. **Store.** The job store is 0600. **Driver.** ax drives it. |
| gateway | `extensions/gateway.py` | **Authorization.** Checked before reading the body, with a constant-time compare against a token of at least 32 characters. **Identity.** The actor is forced to `service:gateway`. **Network.** Loopback only unless explicitly allowed. **Limits.** 1 MiB bodies. |
| TUI | `blux chat` | The terminal chat is the migrated UI. The Ink TUI awaits an owner decision. |

Upstream hook names and authority are not carried over: everything subscribes to Hook System v1 events
(ADR-0012). A feature's parity state is green only with BLUX tests in the matrix's `evidence`. Copying
code never counts, and none was copied: these are clean-room implementations.

## Consequences
- **Matrix states.** Subagents are MIGRATED. The agent loop, skills, plugins, MCP, scheduler, gateway and
  TUI are IN_PROGRESS, each with a named blocker. The open items are:
  - per-skill licence review;
  - re-implementing upstream plugins;
  - HTTP MCP transports;
  - the ax scheduler command;
  - messaging adapters;
  - the full-screen TUI.
- **No fork at runtime.** Nothing here imports or needs the fork. A subprocess test enforces this.
