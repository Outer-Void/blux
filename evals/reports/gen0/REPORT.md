# BLUX Generation 0 baseline

Harness 1.0.0 · BLUX `7bdd076856f8` · Python 3.13.5 · Linux aarch64

| Suite | Status | Cases | Task success | Key results | p50 ms |
|---|---|---|---|---|---|
| policy_risk | MEASURED | 36 | 0.9722 | security regressions: 1 | 0.15 |
| routing | MEASURED | 10 | 1.0 | policy violations: 0 | 2.45 |
| malformed_inputs | MEASURED | 10 | 1.0 | crashes: 0; accepted malformed: 0 | 0.17 |
| reasoning_structure | MEASURED | 50 | 1.0 | security regressions: 0; crashes: 0; adversarial refusal rate: 0.625 | 2.35 |
| construction_repair_dat | MEASURED | 61 | 1.0 | security regressions: 0; crashes: 0; adversarial refusal rate: 0.7778; dat gate pass rate: 0.4; self certification violations: 0; unnecessary edits: 0; repair count: 6 | 177.99 |
| intent | BLOCKED | 8 | — | needs an external model: run with --live and provider credentials (none available: provider accounts have no balance, docs finding F10) | — |
| tool_selection | BLOCKED | 6 | — | needs an external model: run with --live and provider credentials (none available: provider accounts have no balance, docs finding F10) | — |
| repository_diagnosis | BLOCKED | 3 | — | needs an external model: run with --live and provider credentials (none available: provider accounts have no balance, docs finding F10) | — |

Deterministic results must reproduce exactly (`--check`); timings vary by machine.
Model-dependent suites are BLOCKED until provider credentials exist; no score is reported for them.
