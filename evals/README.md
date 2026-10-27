# evals/: BLUX Generation 0 baseline (Stage 35)

Generation 0 = external models + BLUX routing + CogA + cA + DAT + Runtime + BLUX System. This harness measures
it before any native model is trained. A native specialist must beat these numbers, or justify itself by
cost, locality or privacy.

## Run

```sh
python -m evals.blux_eval.run --out evals/reports/gen0 \
  --blux-coga-dataset-python <coga dataset venv>/bin/python \
  --blux-ca-dataset-python <ca dataset venv>/bin/python --dat-bin <dat venv>/bin
# later runs: add --check evals/reports/gen0/report.json (exit 1 on any deterministic difference)
# model-dependent suites: add --live with provider credentials in the environment
```

The dataset interpreters must hold each dataset's pinned engines (`dstool` refuses a version mismatch).
Engine suites replay the protected splits (test, holdout, regression, adversarial) inside the dataset's own
process. Only aggregate metrics leave that process; no protected input or expected output is printed or stored.

## Suites

| Suite | Subject | Status today |
|---|---|---|
| `policy_risk` | `blux_runtime.security.commands.detect` on 36 commands, incl. Android (adb, fastboot) and Termux | MEASURED |
| `routing` | `ModelRouter` with the packaged `default_routes.json`; provider outcomes simulated per case | MEASURED |
| `malformed_inputs` | Model API request checks and Contract v1 schemas on 10 malformed documents | MEASURED |
| `reasoning_structure` | CogA replay of `blux-coga-dataset` protected splits (incl. adversarial) | MEASURED |
| `construction_repair_dat` | cA replay of `blux-ca-dataset` protected splits, with DAT validation inside | MEASURED |
| `intent`, `tool_selection`, `repository_diagnosis` | need an external model | BLOCKED (no provider credentials, docs finding F10) |

Suite data: `evals/suites/*.jsonl`. Policy expectations are what is actually dangerous, not what the detector
does today, so a miss is a real security regression.

## Metrics

Per suite, where they apply:
- **Task success:** exact replay match for engines; correct route or error for routing; correct
  classification for policy; refusal for malformed input.
- **Structural validity:** engine output passes the dataset adapter's schema checks.
- **Security regressions:** dangerous commands not flagged; adversarial records not refused although the
  pinned engine refused them.
- **Policy violations:** a route to an excluded or non-allowed provider; cA self-certification.
- **DAT gate verdicts:** "compile/test pass rate" for cA. The `fail` verdicts are expected: those repair
  fixtures deliberately start from a blocking finding.
- **Unnecessary edits:** changeset operations beyond the recorded result.
- **Repair count, tool calls per case.**
- **Latency** (p50/p95, wall) and **CPU cost.** Engines use no model tokens.

Hallucinated capabilities and token cost are 0 by construction for the deterministic suites. They become
meaningful only for the model suites.

Deterministic results (everything under `deterministic`) must reproduce exactly. `timing` varies by machine and
is informational. `environment.blux_commit` is the commit the harness ran on.

## Limits

Routing quality here means BLUX's routing decisions; provider behaviour is simulated. Engine "task success"
compares a replay with the record the same pinned engine produced: it measures determinism and regressions,
not whether the engine's answer is good. Judging answer quality needs either the model suites or human
review, both still open.
