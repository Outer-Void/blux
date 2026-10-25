# ADR-0010: BLUX Model API v1

- Status: Accepted (Stage 09, 2026-10-25)

## Context
Applications called vendors directly: BluxGPT 1.0 picked `gpt-5-mini`, `claude-haiku-4.5` or
`deepseek-chat` by name, with no timeout, retry, normalization or record. The `blux.model` descriptor
(Contract v1) said models are "invoked through capabilities, never directly", but no such capability existed.

## Decision
1. **Logical models.** Callers target `blux-main`, `blux-fast`, `blux-reason` or `blux-code`. Which
   provider serves a logical model is an operator decision in a `blux.model-routes` table. A request
   carries no vendor name and no credentials.
2. **A separate contract family.** The Model API is versioned on its own (`model_api_version: "1.0"`,
   `contracts/model-api/v1/`, locked by its own `CHECKSUMS.sha256`). Contract v1 is unchanged.
   - Schemas: request, response, error, stream-event, routes and catalog.
   - The SDK packages them byte-identically (`blux_sdk.model_api`).
3. **Through the capability runtime.** `model.generate` (`side_effects: external`, permission `net:model`)
   and `model.catalog` are built-in capabilities. Policy decides who may call a model, and the request and
   response are validated against the Model API schemas. Every call produces a Contract v1 receipt.
4. **Receipts carry safe metadata, never content or secrets.**
   - Each `model.generate` receipt lists one artifact (`application/vnd.blux.model-call+json`) holding the
     call's safe metadata: the logical model and status; the provider, provider model, route and every
     attempt with its error code; usage, timing and determinism; and the output's shape (character count,
     whether it holds JSON, tool names).
   - The artifact holds no prompt, output text or credential. Its labels summarize it.
   - The receipt hashes the request and response as before. Executor support is backward compatible: a
     handler may `attach_artifact`, and a sink may define `emit_artifact`.
5. **Explicit routing, fallback and degradation.**
   - **Order.** Routes are tried in table order. Routes that lack a required capability (explicit, or
     implied by `stream`, `tools` or `response_format`) or are excluded by the request are recorded as
     `skipped`.
   - **Error flags.** Each error code fixes `retryable` (later may succeed) and `fallback` (another route may
     succeed now).
   - **When the router falls back.** Only for `fallback` errors, only when fallback is enabled, and only
     within `max_attempts`. Otherwise the request fails at once with that error and every attempt attached.
   - **No routing around safety refusals.** `CONTENT_FILTERED` never falls back.
   - **Degradation is reported.** A response served by anything but the model's first route is
     `degraded: true`, with its notes and the full attempt list. When every route fails, the error is
     `ALL_ROUTES_FAILED`.
6. **The router checks what backends return.** It validates the response schema, structured output against
   the requested JSON Schema, and tool calls against the declared tools' `input_schema`. A violation is a
   failed attempt of that route (`STRUCTURED_OUTPUT_INVALID`, `TOOL_CALL_INVALID`), so the next route can
   serve the request.
7. **Backends own transport.** A backend is a program speaking the model-backend protocol:
   - **Input.** One JSON object on stdin (`request`, `route`, `context`).
   - **Output.** Stream events as JSON Lines on stdout, ending with `end` or `error`.
   - **Environment.** The child gets a minimal environment plus only the variables the route names in
     `credential_env`.
   - **Cancellation.** On cancellation or deadline, the router terminates and then kills it.
   - **Ownership.** BluxGPT is the reference backend and owns provider transport, retries, streaming,
     timeouts and normalization. BLUX owns routing, policy and receipts.

   BluxGPT has no agent loop: it returns tool calls and never executes them.
8. **Deterministic controls where providers expose them.** `deterministic: true` sets temperature 0 and a
   seed only on routes whose `determinism` flags say the provider honors them. The response reports what
   was requested, what is supported and what was applied. A route without them still serves the request
   (with a note), unless `capabilities` names `deterministic` as a hard requirement.
9. **Errors are normalized and sanitized.** One `blux.model-error` shape. Provider messages pass through a
   credential scrubber before they leave a backend or the router. Through the runtime, a failure is
   `EXECUTION_FAILED` with `details.provider_code` and the `model_error` document.

## Consequences
- An application can switch every provider without code changes by editing the routing table.
- Streaming through the runtime uses `Runtime.execute(on_event=...)`. The CLI writes events only to
  `--events FILE` (ADR-0008 still holds: stdout is one document).
- Adding a capability, field or error code to the Model API needs a `1.x` minor version under ADR-0005 rules,
  applied to this tree.
