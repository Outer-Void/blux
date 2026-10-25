# The `blux` command

`blux` is direct intelligence: ask a logical BLUX model, chat with it, or diagnose the model path.
Ecosystem orchestration (projects, devices, repositories) is `ax`. Design: [ADR-0011](adr/0011-direct-cli.md).

## Asking
```sh
blux "explain this error"
blux explain this error                      # words are joined; quote when the shell would interfere
cat build.log | blux "diagnose"              # piped stdin is attached as the input
git diff | blux "review"
git diff | blux                               # stdin alone is the question
blux --model blux-code "write a jq filter for the newest tag"
blux -s "answer in one sentence" "what is a receipt?"
blux --schema verdict.json "is this safe?"   # structured output, printed as JSON
blux -- version                               # a prompt that is also a subcommand name
```

Logical models: `blux-main` (default), `blux-fast`, `blux-reason`, `blux-code`. Which provider serves each
one is set by the routing table (`blux models` shows it), never by the command.

## Chat
```sh
blux chat
blux chat --model blux-reason
printf 'hello\nand again\n' | blux chat --json   # piped: one turn per line, one blux.result per turn
```
Commands: `/model NAME`, `/system TEXT`, `/reset`, `/last` (routing of the last answer), `/exit`. Ctrl-C
cancels the answer in progress; Ctrl-D leaves.

## Output
- **Human mode (default).** The answer on stdout, streamed when stdout is a terminal (`--stream` or
  `--no-stream` to choose).
  - **Footer.** Diagnostics go to stderr. When stderr is a terminal, a footer gives model,
    provider/model, time, tokens and operation id. `-v` adds receipt, session and every route attempt,
    and `-q` drops the footer.
  - **Failures.** The message, each failed route with its error code, a hint, and the operation and
    receipt ids.
- **Machine mode (`--json`).** One `blux.result` document on stdout (`chat`: one per turn). Use it from
  scripts. With `--pretty`, the JSON is indented.

## Exit codes
| Code | Meaning |
|---|---|
| 0 | answered |
| 1 | the model call failed: provider error, every route failed, refusal, policy |
| 2 | usage, or input that cannot be sent: empty, binary, not UTF-8, over the size limit |
| 3 | configuration: config file, routes table, missing backend, or credentials missing or rejected on every route |
| 4 | timed out (`--timeout`, capped by the routing table) |
| 5 | answered, but the receipt was not recorded by BLUX System |
| 130 | cancelled (Ctrl-C) |

## Configuration
`~/.config/blux/config.toml` (or `$XDG_CONFIG_HOME/blux/config.toml`, or `$BLUX_CONFIG`):
```toml
model = "blux-main"
timeout_seconds = 60
# routes = "~/.config/blux/routes.json"          # default: $BLUX_MODEL_ROUTES or the packaged table
# system_store = "~/.local/share/blux/system"    # record receipts in BLUX System
stream = true
footer = "auto"            # auto (when stderr is a terminal) | always | never
redact_input = true        # replace credential-shaped strings before sending
max_input_bytes = 1048576
```
Flags win over environment variables (`BLUX_MODEL`, `BLUX_TIMEOUT`, `BLUX_MODEL_ROUTES`,
`BLUX_SYSTEM_STORE`), which win over the file. `blux config` shows each value and where it came from.

**Credentials never go here.** The model backend owns them (BluxGPT: `~/.config/bluxgpt/.env`, see
`bluxgpt status`). A credential-like key or value in the BLUX config file is refused (exit 3).

## Diagnostics
```sh
blux doctor                 # config, routes, backends on PATH, credential presence per route
blux doctor --probe         # also send a tiny prompt through every route (spends a few tokens)
blux doctor --json -m blux-main
blux models                 # the catalog: logical models and what their routes provide
```
`doctor` exits 0 when every logical model has an installed backend and 3 when one does not. With
`--probe`, it exits 1 when some logical model has no working route.

## Receipts
Every call is a `model.generate` execution with a receipt.
- **Linkage.** A chat is one session, and each turn's parent is the previous turn.
- **Recording.** Set `system_store` (or `--system-store`) to record receipts and their model-call
  metadata in BLUX System.
- **Content.** Receipts hold routing, usage and timing, never prompts, input, answers or keys.
