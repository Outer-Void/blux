# Generation 2 (coding and reasoning specialists): readiness gap (Stage 37)

Verdict on 2026-10-27: **no Generation 2 model is trained.** Training is not justified yet. This page records
the exact gap for each target, what is already in place, and what must exist before any training starts. It
builds on the Generation 0 baseline (`evals/reports/gen0/`) and on the Stage 36 register
([SPECIALISTS.md](SPECIALISTS.md)).

## Readiness by target

| Target | Eligible training data today | Baseline to beat | Gap that blocks training |
|---|---|---|---|
| Repository diagnosis | None (3 evaluation cases in `evals/suites/repo_diagnosis.jsonl`) | Not measurable: the Gen 0 model suite is BLOCKED (no provider credentials, F10) | Owner-authored diagnosis cases with verified root causes (target ≥ 200 families + fresh holdout); a measured external-model baseline |
| Code repair | cA records are engine-generated deterministic replays (`blux-ca-dataset`, 61 protected) | cA replays exactly (1.0) with DAT validation; external models unmeasured | Real failing-repository cases with test and DAT outcomes, and multiple candidate repairs per case; imitating deterministic cA output adds nothing |
| Architecture decomposition | CogA `reason.decompose` records (8 train/validation records, 3 families) | CogA replay 1.0 | Independent decomposition tasks with reviewed reference trees; far more than 3 families |
| Test generation | None | None measured | A corpus of code under test with mutation-score labels (generated tests must kill mutants), licensed and contamination-checked |
| Structured reasoning | CogA records (106 total; 56 train/validation, all engine-generated) | CogA replay 1.0; the engine is deterministic by design | A task where a model adds value over the deterministic engine (for example drafting the structured request); no such evaluation exists |
| Deterministic-output assistance | None | Schemas and engines enforce determinism already | A defined task and success metric (for example valid structured output on the first try), measured on external models first |

Compute: the only environment is aarch64 under PRoot (CPU, no GPU). That suffices for the small linear
baselines of Stage 36. It does not suffice for coding or reasoning models of a useful size, so
Generation 2 training also needs owner-provided GPU compute.

## Requirements already in place

| Requirement | Where it is met |
|---|---|
| Proprietary-by-default licensing and provenance | `blux_training` licence gate (allow-list; artifacts each need a licence); dataset repositories are proprietary; Outer-Void/credits policy |
| Credits repository policy, travelling notices | Outer-Void/credits (`tools/validate.py --org-root`); AI-assistance records per repository |
| AI/tool/model/inspiration attribution | Credits `provenance/ai-assistance.yaml` and the attribution graph; unverified tooling is recorded as unresolved (UNR-021) |
| Contamination controls | `blux_training` contamination gate (input containment against protected sets); dataset canaries and split locks (`dstool`); Router 0.x family isolation |
| Reproducible training | `blux_training` deterministic builds; Router 0.x seeded, frozen configuration and reproduce command |
| Fixed holdout | Dataset split locks; `blux_training` split lock (a record never moves); Router reservation refuses a second protected evaluation |
| Adversarial and security evaluation | Gen 0 adversarial splits (CogA and cA) and the policy/risk suite |
| Integration through stable logical model IDs | Model API logical models `blux-main`, `blux-fast`, `blux-reason`, `blux-code`; a native model would be one more route |
| Rollback to the prior backend | Every logical model has at least one fallback route; a native primary is rolled back by editing `default_routes.json`. Router fallback is tested (`tests/models/test_model_api.py`) |
| No application-level dependency on one physical model | Enforced by `tests/models/test_logical_model_isolation.py`: no tracked code names a physical model; each logical model has a fallback |

## What must happen before any Generation 2 training

1. Provider credentials, so the Gen 0 model suites produce a real external-model baseline (F10, F60).
2. Owner-authored, rights-cleared task corpora for one target, with separately authored and unopened
   holdout, adversarial and regression sets. Repository diagnosis and code repair have the clearest value.
3. GPU compute, sized to a stated model family and budget.
4. A predeclared acceptance threshold against the Gen 0 baseline, for example: beats the external model
   on the holdout at lower cost or local-only, with no security regression on the adversarial set.

Until then, the honest Generation 2 position is: infrastructure ready, data, baseline and compute missing.
