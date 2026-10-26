# ADR-0012: BLUX Hook System v1

- Status: Accepted (Stage 12, 2026-10-26)

## Context
BLHermes (Hermes Agent) has hooks: in-process plugin callbacks with access to the agent's credentials
and no typed contract (Stage 11 inventory, BLH-016). BLUX needs extension points for plugins, skills
and MCP servers, and later for the agent layer. Those hooks must not become a second authority beside
policy, and they must not be opaque Hermes hooks carried forward.

## Decision
1. **Typed, versioned events.**
   - **Contract.** `contracts/hooks/v1/` is its own contract family (locked by `CHECKSUMS.sha256` and
     packaged with the runtime). `events.schema.json` defines 37 events in 13 families: runtime, session,
     request, model, reason, tool, capability, artifact, receipt, agent, security, credential and
     training.
   - **Payloads.** Each event's payload has its own schema. An `x-blux-hook` annotation gives its phase,
     whether it is vetoable, its mutable fields and their rule, its sensitive fields, and its emitter.
   - **Receipts and registrations.** `receipt.schema.json` and `registration.schema.json` define audit
     receipts and external subscriber manifests.
2. **BLUX owns the bus.** `blux_runtime.hooks.HookBus` is internal. BLUX components emit: the executor,
   the model router, and owners such as the future agent layer and datasets.
3. **What a hook can do.** It can observe an event. It can veto a vetoable `before` event, if it holds
   `veto:<event>`. It can change a declared mutable field, but only in that field's narrowing direction,
   if it holds `mutate:<event>.<field>`. There are two narrowing rules:
   - `model.before_route.exclude_providers` may only grow.
   - `tool.before_execute.timeout_ms` may only decrease.
4. **What a hook can never do.**
   - **No allow result.** There is no allow, authorize or grant result. Returning anything other than
     a `HookResult` is a protocol violation.
   - **No elevation.** No hook may change identity, side effects, policy or device fields. An attempt is
     recorded as an elevation violation.
   - **Policy always decides.** A veto denies, but nothing turns a policy deny into an allow.
   - **Device operations.** Events about device operations (`device_operation: true`) accept no mutation
     at all. Hooks can never authorize a destructive device operation; they can only stop one.
5. **Determinism.**
   - **Order.** Handlers run one at a time, by descending priority and then registration order. External
     sources are capped at ±100, so BLUX's own guards (up to ±1000) always run first.
   - **Sequence.** Every emission gets a bus-wide sequence number.
6. **Sync and async.**
   - **Calls.** `emit()` and `aemit()` accept both plain and `async` handlers.
   - **Timeouts.** Each handler has a timeout (default 1 s, at most 30 s). A timed-out handler is abandoned
     and nothing it returns is applied.
7. **Failure isolation and safe cancellation.**
   - **Isolation.** A handler's exception, timeout or bad return value never breaks the caller or other
     hooks. Three consecutive failures quarantine the hook.
   - **Failure policy.** `failure_policy: deny` fails closed. It is allowed only for hooks holding the veto
     on vetoable events.
   - **Cancellation.** Cancelling an emission skips the remaining handlers. On a vetoable event the
     operation does not proceed, because an interrupted guard never counts as approval.
8. **Recursion protection.**
   - **Depth.** Emission depth is tracked across threads and async tasks, with a default limit of 4. At
     the limit no handler runs, and a vetoable event does not proceed.
   - **Re-entry.** A hook is not re-entered for the event it is handling.
   - **Emitting.** A hook may emit only with `emit:<family>`, and only builtin hooks can hold that.
9. **Permissions and source identity.** Every hook declares a source (`builtin`, `project`, `plugin`,
   `skill` or `mcp`, with id, version and digest). A `HookPolicy` grants permission patterns per source.
   External sources get nothing unless granted, and can never hold `emit:`. A refused registration emits
   `security.violation`.
10. **Secret-safe payloads.**
    - **Scrubbing.** Payload strings are scrubbed with the SDK's credential patterns before any hook
      sees them.
    - **Sensitive fields.** Messages and tool arguments are masked unless the hook holds
      `read_sensitive:<event>`.
    - **Credentials.** `credential.requested` carries variable names only.
11. **Audit receipts.**
    - **Per emission.** Every emission produces a `blux.hook-receipt`. It lists each handler, its source,
      priority, outcome, duration and the mutated field names, together with the proceed or veto
      decision and the payload hashes before and after. Payload values never appear.
    - **Per operation.** The executor attaches an operation's hook receipts to it as an artifact
      (`application/vnd.blux.hook-receipts+json`), so they reach BLUX System with the operation receipt.
12. **Adapters, not access.**
    - **Registration.** Plugins, skills and MCP servers register through `ExternalSubscribers` with a
      `blux.hook-registration` manifest. Events are delivered over a transport: in-process callable, or
      one subprocess per event with a minimal, credential-free environment.
    - **Verdicts.** The subscriber answers `ok`, `veto` or `mutate`.
    - **Limits.** Adapters cannot emit, cannot register builtin hooks and cannot remove BLUX-owned hooks.

## Where events are emitted (Stage 12)
- **Runtime lifecycle.** `Runtime.start()`/`stop()` (or `with runtime:`) emit `runtime.*` and
  `capability.discovered`.
- **Every execution.** `request.*`, `capability.before_execute`/`after_execute`, `artifact.created`
  (handler artifacts) and `receipt.created`.
- **Tools and reasoning.** For capabilities labelled `kind: tool`, the `tool.*` events. For capabilities
  labelled `kind: reason`, `reason.before` and `reason.after`.
- **Model router.** `model.before_route`, `model.after_route`, `model.before_call` and
  `model.after_call`, plus `credential.requested` when a route names credential variables.
- **Defined but owner-emitted.** These events are defined and tested, but their owners emit them when
  those exist: `session.*` (the CLI and agent sessions), `agent.*` (the agent layer), `artifact.modified`
  and `artifact.validated` (artifact owners, e.g. DAT), and `training.*` (the dataset tools).
- **The bus.** `security.violation` is emitted by the bus only.

## Consequences
- Hermes-style hooks migrate as subscribers with explicit grants (Stage 11 matrix BLH-015/016/017). They
  never get in-process access to credentials or the bus.
- A veto shows up as `POLICY_DENIED` with `details.denied_by = "hook"`. In the Model API it shows up as
  `INVALID_REQUEST` with the same detail.
- Abandoned timed-out sync handlers keep their thread until they return, which is why the breaker
  quarantines hooks that keep timing out.
