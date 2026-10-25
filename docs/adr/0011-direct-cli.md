# ADR-0011: The direct `blux` intelligence CLI

- Status: Accepted (Stage 10, 2026-10-25)

## Context
Stage 09 made logical models callable through the Model API (`model.generate`), but only through the
machine command `blux generate MODEL PROMPT`, which prints a `blux.result` document. People asking a
question, piping a log or reviewing a diff need an answer, not an envelope. BluxGPT 1.0 had filled that
gap with a zsh `blux()` function that shadowed the CLI and called vendors directly. That function was
removed in BluxGPT 2.0.

## Decision
1. **Permanent boundary.** `blux` is direct intelligence: ask a logical model, chat, diagnose the model
   path. `ax` is ecosystem orchestration. `blux` gains no project, device, repository or ecosystem
   management commands. Those belong to `ax`, which may call `blux` or the Model API.
2. **The first word decides.** If the first word (after `--pretty`) is a subcommand, the existing
   machine protocol applies (ADR-0008). Otherwise the words are a prompt: `blux explain this error` means
   `blux ask explain this error`. `blux -- version` asks about the word "version". A bare `blux` with piped
   stdin asks about the input.
3. **Direct commands.**
   - `ask`: one question; piped stdin is attached as `<input source="stdin">`.
   - `chat`: a conversation (interactive on a terminal; one turn per line when stdin is piped).
   - `doctor`: config, routes, backends and credential presence; `--probe` calls every route.
   - `config`: the effective settings and where each came from.
4. **Through the Model API and the runtime only.** Every call is a `model.generate` execution with the
   built-in model policy. It is validated and receipted like any other caller's (ADR-0010). The CLI never
   names a provider or holds a key. It selects a logical model (`--model`), and the routing table decides
   the provider.
5. **Two output modes.**
   - **Human (default).** The answer on stdout, streamed when stdout is a terminal (`--stream` forces
     it, `--no-stream` disables it). Everything else goes to stderr: a footer with model, provider, time,
     tokens and operation id (`-v` adds receipt, session and attempts), and errors with each failed route
     and a hint.
   - **Machine (`--json`).** `ask` prints exactly one `blux.result`. `chat` prints one per turn (JSON
     Lines), and `doctor` and `config` print one document. A usage or configuration problem in machine
     mode is a `blux.error` document whose `details` carry `reason` and `exit_code`.
   - **Cut-off streams.** If a route fails mid-stream and the router falls back, stderr says that the
     streamed text above is incomplete.
6. **Exit codes (direct commands).** `0` ok · `1` the model call failed (provider error, every route
   failed, refusal, policy) · `2` usage or unusable input · `3` configuration: an unreadable or invalid
   routes table or config file, a missing backend, or missing or rejected credentials on every route ·
   `4` timed out · `5` answered, but the receipt was not recorded by BLUX System (fail closed) · `130`
   cancelled. The contract subcommands keep ADR-0008's `0/1/2`.
7. **Timeouts and cancellation.** `--timeout SECONDS` (or config) sets the request's `timeout_ms`. The
   routing table's default is the upper bound. Ctrl-C cancels the call in flight: the router stops the
   backend process and the CLI exits 130. A second Ctrl-C interrupts at once. In interactive chat,
   Ctrl-C cancels only the current answer.
8. **Safe configuration and credentials.**
   - **Config file.** `$BLUX_CONFIG`, else `$XDG_CONFIG_HOME/blux/config.toml`, else
     `~/.config/blux/config.toml`. It holds preferences only: `model`, `timeout_seconds`, `routes`,
     `system_store`, `stream`, `footer`, `redact_input` and `max_input_bytes`.
   - **Precedence.** Flag, then environment (`BLUX_MODEL`, `BLUX_TIMEOUT`, `BLUX_MODEL_ROUTES`,
     `BLUX_SYSTEM_STORE`), then file, then default.
   - **No credentials.** A credential-like key or value in the file is a configuration error, and the
     value is never echoed. Provider credentials belong to the backend (BluxGPT:
     `~/.config/bluxgpt/.env`). The backend child process gets only the variables a route names in
     `credential_env`.
   - **Doctor.** `doctor` reports credential presence, never values.
   - **Redaction.** Credential-shaped strings in the prompt or piped input (the SDK's `SECRET_PATTERNS`)
     are replaced with `[redacted]` before anything leaves the machine, and stderr says so. `--no-redact`
     sends them as-is.
   - **Input limits.** Piped input is capped at `max_input_bytes` (default 1 MiB) and must be UTF-8 text.
9. **Receipt linkage.**
   - **Ask.** Each call gets an operation id and a receipt id, shown in the `-v` footer and on every
     failure.
   - **Chat.** A chat is one session, and each turn's `parent_operation_id` is the previous turn.
   - **Recording.** With `system_store` set, receipts and their model-call artifacts are recorded in
     BLUX System, and an unrecorded receipt exits 5.
   - **Content.** Receipts never hold prompts, input or answers (ADR-0010).

## Consequences
- `blux "..."` works in pipes (`git diff | blux review`, `cat build.log | blux diagnose`) without
  envelope parsing, while scripts keep a stable machine mode.
- Prompt text that collides with a subcommand name needs `--` or `blux ask`.
- The CLI's quality depends on the routing table. `blux doctor --probe` is the way to check a real setup,
  and it spends a few tokens per route.
- Python 3.10 needs `tomli` for the config file (a conditional dependency).
