# ADR-0008: No arbitrary stdout as machine protocol

- Status: Accepted (Stage 01, 2026-10-24)

## Context
Several ecosystem tools mix human text, colors and machine output on stdout, so integrations would have to
scrape it.

## Decision
- Machine interfaces exchange **contract documents**, not log lines.
- The `blux` CLI writes **exactly one JSON document** to stdout per invocation, newline-terminated. `--pretty` indents it but it is still one document.
- Diagnostics, progress and usage go to **stderr**.
- Exit codes: `0` ok, `1` contract-level failure (the result/error document is still on stdout), `2` usage error (stdout empty).
- Side outputs go to explicit files (`--receipt-out`), never interleaved on stdout.
- A capability with `provider.kind: subprocess` must follow the same rule: one `blux.result`-compatible JSON object on stdout. Anything else is `OUTPUT_VALIDATION_FAILED`.

`tests/cli/test_cli.py` enforces the single-document rule.
