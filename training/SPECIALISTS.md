# Native specialist register (Stage 36)

Every Generation 1 candidate from the plan, with the ten required points. Verdicts are evidence, not intent:
nothing is integrated unless it beats the Generation 0 baseline (`evals/reports/gen0/`) on a permanent holdout.
Machine-readable copy: [specialists.json](specialists.json) (checked by `tests/training/test_specialists.py`).

| Candidate | Verdict | Baseline | Why |
|---|---|---|---|
| capability-routing | **REJECTED_WITH_EVIDENCE** | Deterministic keyword routing 86/124 (69.35%), macro F1 0.5302; majority baseline also measured. | Not integrated; Model API routing unchanged. Needs 694 independent owner-authored families (training/research/WAKE_UP_REPORT.md section 39). |
| risk-classification | **REJECTED_WITH_EVIDENCE** | Deterministic blux_runtime.security.commands.detect: 36/36 after the Stage 36 wipe-rule fix (35/36 before), 0 false alarms. | Rejected: a learned classifier cannot beat an auditable rule at 100% on the suite and would weaken fail-closed behaviour. Improve the rules instead (as done for adb wipe). |
| verdict-classification | **REJECTED_WITH_EVIDENCE** | DAT itself: deterministic gate (fail_on, min_confidence), 4 pass / 6 fail exactly reproduced. | Rejected: replacing or pre-empting DAT's deterministic verdict with a model would break the authority boundary (cA never certifies; DAT validates). |
| intent-classification | **BLOCKED_DATA** | BLOCKED: the external-model baseline needs provider credentials (docs finding F10). | Not possible until a baseline and an owner-authored corpus exist. |
| tool-selection | **BLOCKED_DATA** | BLOCKED: external-model baseline needs credentials (F10). | Blocked on the same data as capability routing. |
| repair-ranking | **BLOCKED_DATA** | cA's single deterministic repair. | Needs multi-candidate repair generation plus DAT/test outcome labels. |
| code-patch-ranking | **BLOCKED_DATA** | Running the tests (exact, slower). | Needs a patch-candidate corpus with recorded test and DAT outcomes. |
| structured-planning-classification | **COVERED_BY_ROUTING_STUDY** | Keyword routing (same study). | Not integrated (same verdict). |

No specialist is integrated. The Model API, routing table and capability registry are unchanged.
The capability-routing study, the only trained candidate, is documented in [research/WAKE_UP_REPORT.md](research/WAKE_UP_REPORT.md),
and its blocker is owner-authored, rights-cleared evaluation data, not compute.

## capability-routing: REJECTED_WITH_EVIDENCE

- **task:** Natural-language task -> engine family -> capability (14 classes) or ABSTAIN.
- **why native:** Local, offline, cheap routing before any provider call; keeps task text on device.
- **data source:** blux-coga-dataset and blux-ca-dataset task instructions (owner-licensed, pinned commits); compiled as blux-routing-0.2 outside Git.
- **splits:** 124 eligible train/validation records; fresh test/adversarial/regression/holdout: 0 (Router 0.2).
- **baseline:** Deterministic keyword routing 86/124 (69.35%), macro F1 0.5302; majority baseline also measured.
- **architecture:** TF-IDF + L2 logistic regression heads (CPU, <100 records); no pretrained weights.
- **training config:** training/router/ (seeded, frozen config, artifact policy); training/experiments/blux-router-0.1/.
- **trained:** Yes (Router 0.1; Router 0.2 development studies).
- **holdout evaluation:** Router 0.1 failed its one-shot protected evaluation; Router 0.2 BLOCKED_DATA (no fresh protected families).
- **measurements:** Best hybrid (learned family + keyword capability) 93/124 (75.0%) grouped development, gain interval 0 to 12.77 pp includes zero.
- **integration:** Not integrated; Model API routing unchanged. Needs 694 independent owner-authored families (training/research/WAKE_UP_REPORT.md section 39).
- **evidence:** `training/research/WAKE_UP_REPORT.md`, `training/research/experiments.jsonl`, `training/experiments/AUDIT.md`

## risk-classification: REJECTED_WITH_EVIDENCE

- **task:** Shell/device command -> dangerous? device-affecting? rule.
- **why native:** Would only help on commands no rule anticipates.
- **data source:** evals/suites/policy.jsonl (36 owner-authored ground-truth cases); no larger labelled corpus exists.
- **splits:** Not trainable: 36 cases are an evaluation suite, not a training set.
- **baseline:** Deterministic blux_runtime.security.commands.detect: 36/36 after the Stage 36 wipe-rule fix (35/36 before), 0 false alarms.
- **architecture:** None selected.
- **training config:** None.
- **trained:** No (not justified).
- **holdout evaluation:** Not applicable.
- **measurements:** Rule: p50 well under 1 ms, auditable, fails closed on unparseable input.
- **integration:** Rejected: a learned classifier cannot beat an auditable rule at 100% on the suite and would weaken fail-closed behaviour. Improve the rules instead (as done for adb wipe).
- **evidence:** `evals/reports/gen0/report.json`, `runtime/blux_runtime/security/commands.py`

## verdict-classification: REJECTED_WITH_EVIDENCE

- **task:** Predict DAT's verdict (pass/fail) for a change.
- **why native:** None: DAT is the independent validation authority; prediction is not validation.
- **data source:** DAT reports inside blux-ca-dataset (10 gate verdicts in protected splits).
- **splits:** Not trainable (10 verdicts).
- **baseline:** DAT itself: deterministic gate (fail_on, min_confidence), 4 pass / 6 fail exactly reproduced.
- **architecture:** None.
- **training config:** None.
- **trained:** No.
- **holdout evaluation:** Not applicable.
- **measurements:** DAT verdicts reproduce exactly in the Gen 0 replay.
- **integration:** Rejected: replacing or pre-empting DAT's deterministic verdict with a model would break the authority boundary (cA never certifies; DAT validates).
- **evidence:** `evals/reports/gen0/report.json`

## intent-classification: BLOCKED_DATA

- **task:** User request -> BLUX intent (diagnose, construct, repair, compare, plan, device, risk, analyze).
- **why native:** Local first-hop classification without a provider call.
- **data source:** None eligible yet; evals/suites/intent.jsonl holds 8 evaluation cases only.
- **splits:** None.
- **baseline:** BLOCKED: the external-model baseline needs provider credentials (docs finding F10).
- **architecture:** If data arrives: the Router 0.x linear baseline first.
- **training config:** Not written.
- **trained:** No.
- **holdout evaluation:** Not run.
- **measurements:** None.
- **integration:** Not possible until a baseline and an owner-authored corpus exist.
- **evidence:** `evals/suites/intent.jsonl`, `evals/reports/gen0/report.json`

## tool-selection: BLOCKED_DATA

- **task:** Task -> capability/tool to invoke (overlaps capability routing).
- **why native:** Fewer provider tool-call round trips.
- **data source:** None beyond the routing corpus; evals/suites/tool_selection.jsonl has 6 cases.
- **splits:** None.
- **baseline:** BLOCKED: external-model baseline needs credentials (F10).
- **architecture:** Shares the capability-routing study.
- **training config:** See capability-routing.
- **trained:** No.
- **holdout evaluation:** Not run.
- **measurements:** None.
- **integration:** Blocked on the same data as capability routing.
- **evidence:** `evals/suites/tool_selection.jsonl`, `training/research/WAKE_UP_REPORT.md`

## repair-ranking: BLOCKED_DATA

- **task:** Rank candidate repairs for a failing artifact.
- **why native:** Choose among cA candidates without a model call.
- **data source:** None: cA emits one changeset per step, so no candidate sets with outcome labels exist (6 repairs in the Gen 0 replay).
- **splits:** None.
- **baseline:** cA's single deterministic repair.
- **architecture:** Not selected.
- **training config:** None.
- **trained:** No.
- **holdout evaluation:** Not run.
- **measurements:** None.
- **integration:** Needs multi-candidate repair generation plus DAT/test outcome labels.
- **evidence:** `evals/reports/gen0/report.json`

## code-patch-ranking: BLOCKED_DATA

- **task:** Rank candidate code patches by expected test/DAT outcome.
- **why native:** Cheap local pre-filter before running tests.
- **data source:** None: no candidate patch sets with test outcomes exist.
- **splits:** None.
- **baseline:** Running the tests (exact, slower).
- **architecture:** Not selected.
- **training config:** None.
- **trained:** No.
- **holdout evaluation:** Not run.
- **measurements:** None.
- **integration:** Needs a patch-candidate corpus with recorded test and DAT outcomes.
- **evidence:** `evals/reports/gen0/report.json`

## structured-planning-classification: COVERED_BY_ROUTING_STUDY

- **task:** Request -> CogA planning operation (plan, decompose, compare, rank, risk, analyze, critique).
- **why native:** Same as capability routing for the reason.* family.
- **data source:** The reason.* labels of the capability-routing corpus.
- **splits:** See capability-routing (reason.rank has 3 train records, 0 validation).
- **baseline:** Keyword routing (same study).
- **architecture:** Router family head.
- **training config:** training/router/.
- **trained:** Yes, as part of the routing study.
- **holdout evaluation:** As capability-routing.
- **measurements:** As capability-routing.
- **integration:** Not integrated (same verdict).
- **evidence:** `training/research/WAKE_UP_REPORT.md`
