# ADR-0009: Subprocess capability providers

- Status: Accepted (Stage 03, 2026-10-24)

## Context
Standalone repositories (DAT first) expose capabilities in `.blux/` with `provider.kind: subprocess`.
ADR-0008 fixed the output rule (one JSON object on stdout) but not the request, the error shape or the
process boundary. BLUX could not run any capability outside this repository.

## Decision
`blux_runtime.subprocess_provider.SubprocessProvider` runs `provider.entrypoint`:

- **argv, not a shell.** The entrypoint is split with `shlex`. `argv[0]` is resolved on the child's `PATH`.
- **Request on stdin:** `{"capability_id", "input", "context": {operation_id, parent_operation_id,
  session_id, project_id, capability_id, actor, timeout_ms}}`. Input has already passed `input_schema`
  and policy.
- **Response on stdout: exactly one JSON object** (max 16 MiB).
  - exit `0`: the object is the capability output. It is still validated against `output_schema`.
  - exit `1` with `{"error": {"code", "message", "details"}}`: `EXECUTION_FAILED`, with
    `details.provider_code`/`provider_details` carrying the provider's own code.
  - Any other exit: `EXECUTION_FAILED` (`reason: nonzero_exit`).
  - Unparseable or extra output: `OUTPUT_VALIDATION_FAILED` on exit 0, otherwise `EXECUTION_FAILED`
    (`reason: protocol_violation`).
  - Missing executable: `EXECUTION_FAILED` (`reason: executable_not_found`). Timeout kills the child: `TIMEOUT`.
- **stderr is discarded.** It never enters a result or receipt.
- **Minimal environment:** `PATH`, `HOME`, locale, temp dirs. The caller's credentials are not inherited.
- Handlers may now raise `OUTPUT_VALIDATION_FAILED` as well as `EXECUTION_FAILED`/`TIMEOUT`. All three are
  failures, never rejections, so ADR-0006's "handlers cannot impersonate rejections" still holds.

`blux run|exec|capabilities --project DIR` registers DIR's `.blux/` capabilities next to the built-ins.
Without `--policy`, the project's own `policy.yaml` is used instead of the reference policy. Loading
remains inert: nothing runs until a request passes policy. Provider kind `python` is declared in the
schema but not implemented by this runtime, and fails at call time with `reason: unsupported_provider_kind`.

## Consequences
- A provider's domain result (for example a failed DAT gate) is a *successful* capability call with a
  negative verdict in its output. A provider *failing to do its job* is `EXECUTION_FAILED`. BLUX System
  therefore records them as `SUCCESS` and `FAILURE` respectively.
- `tests/runtime/test_subprocess_provider.py` covers the protocol; `tests/integration/test_dat.py` is
  the Stage 03 hard gate against the real `dat` and `blux-system`.
