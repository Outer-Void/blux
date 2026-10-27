# BLUX Native Model Program — Phase 1 report

**Outcome: experiment closed; integration gate FAILED. Normal BLUX routing is unchanged.**

The first narrow learned router was implemented, trained, exported and evaluated. It did not
outperform the deterministic keyword baseline on TEST or HOLDOUT and did not establish useful
confidence-based coverage. This is failure evidence, not a model release or a foundation-model claim.

## 1. Repository and architecture audit

See [AUDIT.md](AUDIT.md) for all five exact starting main commits, discovered contracts, routing,
capability registry, permissions, receipt ownership and data provenance. All starts matched fetched
origin/main; only BLUX was edited. Model API provider routing and engine-capability routing are distinct.
BLUX remains intelligence/integration infrastructure; Outer Void remains federated. No engine, DAT,
control-plane, provider or execution authority moved into this classifier.

## 2. Dataset integrity and counts

Both repository validators and byte-identical pinned-engine rebuild checks passed. Schemas, record
hashes, aggregate manifest digests, immutable split locks, family grouping, provenance, privacy,
hidden-reasoning gates and original contamination controls passed. Additional normalized instruction
exact-duplicate checking found zero cross-split collisions. No protected expected outputs were manually
inspected for feature design; mechanical integrity validation necessarily parses them.

| Dataset | train | validation | test | adversarial | regression | holdout |
|---|---:|---:|---:|---:|---:|---:|
| blux-ca-dataset | 51 | 20 | 30 | 9 | 8 | 14 |
| blux-coga-dataset | 42 | 14 | 22 | 8 | 8 | 12 |
| Combined raw | 93 | 34 | 52 | 17 | 16 | 26 |
| Single-label eligible | 90 | 34 | 52 | 17 | 16 | 26 |

Three cA train records contain multiple distinct capabilities and are excluded rather than assigned an
invented route. There are no other single-label exclusions. Metrics cover eligible records only.
Protected records never entered fit, vocabulary, features, calibration, augmentation or threshold selection.

| Dataset | Manifest SHA-256 | Aggregate records SHA-256 |
|---|---|---|
| blux-ca-dataset | `8634d3d6a5342bdf340ee1dfbc38acc39cb79fe4a85e078f3d6c062f8356eb72` | `b894a01a6e00688bb680374be28d754fe65921442185b3d39ea5b78b9dfb2a3a` |
| blux-coga-dataset | `ce43e4449d0c76a857af17bc415ed9b54f3be4ffeb321f0ef4650a859be667b3` | `725c56f049bf65a28ba23b729a8b9d3b2d493a6fbdf5a9ae50a89e0f8e4e8fae` |

## 3. Exact label set

Derived from engine source and cross-checked against federated descriptors:

| Family | Current capabilities |
|---|---|
| ca | `artifact.construct`, `artifact.modify`, `artifact.repair`, `artifact.validate`, `code.patch`, `config.generate`, `repo.patch` |
| coga | `reason.analyze`, `reason.compare`, `reason.critique`, `reason.decompose`, `reason.plan`, `reason.rank`, `reason.risk` |

All seven CogA and five cA labels occur in training. `artifact.validate` and `repo.patch` lack
single-label training support and are explicitly unsupported in the model. They remain in the 14-class
metrics universe and contribute zero recall when present. A source label does not imply training coverage.

## 4. Baselines, candidate and frozen configuration

Majority uses training class frequencies. Keyword rules come from deterministic capability contracts;
no protected examples informed them. Both baselines were measured and saved before candidate fitting.
The candidate is hierarchical TF-IDF plus three L2 logistic heads: family and per-family capability.
There are 1425 word unigram/bigram features. Seed 1729; C=4; balanced class weights;
lbfgs; max_iter=2000; one numerical thread. Vocabulary and IDF use train only. No pretrained model,
large model download or transformer was used. Python 3.12.14 is the reference platform.

Validation selected temperature **0.5** by joint NLL. None of the frozen threshold choices met ≥90%
accepted accuracy with at least 8 examples, so threshold **1.0** was retained. No feature, parameter,
calibration or threshold changed after freeze or protected evaluation.

| Dependency | Version |
|---|---|
| PyYAML | 6.0.3 |
| joblib | 1.5.2 |
| jsonschema | 4.26.0 |
| numpy | 2.3.4 |
| scikit-learn | 1.7.2 |
| scipy | 1.16.3 |
| threadpoolctl | 3.6.0 |

The scientific training dependencies are optional and pinned in `training/requirements.txt`.
NumPy/SciPy provide numerical routines, sklearn fits the small linear heads, joblib/threadpoolctl are
pinned training dependencies. Existing PyYAML/jsonschema support repository contracts. Runtime package
dependencies did not change; exported inference runs with Python `-S` (no site-packages).

## 5. Metrics and protected protocol

Order: freeze → allowed-data reproduction → TEST → ADVERSARIAL → REGRESSION → HOLDOUT → close.
Protected evaluation ran once. Later reproduction reopened only train/validation. Counts below are
single-label eligible records. Accuracy is diagnostic full-coverage argmax accuracy, even when the
candidate abstains. Macro F1 averages all 14 authoritative classes, including absent classes as zero.

| Split (n) | Majority accuracy / macro F1 | Keyword accuracy / macro F1 | Candidate accuracy / macro F1 | Candidate family accuracy | Candidate coverage |
|---|---:|---:|---:|---:|---:|---:|
| validation (34) | 35.29% / 0.0373 | 58.82% / 0.3690 | 70.59% / 0.3878 | 94.12% | 0.00% |
| test (52) | 34.62% / 0.0367 | 65.38% / 0.4196 | 50.00% / 0.2545 | 92.31% | 0.00% |
| adversarial (17) | 52.94% / 0.0495 | 82.35% / 0.2714 | 58.82% / 0.0945 | 88.24% | 0.00% |
| regression (16) | 12.50% / 0.0159 | 87.50% / 0.4048 | 37.50% / 0.1310 | 100.00% | 0.00% |
| holdout (26) | 38.46% / 0.0397 | 69.23% / 0.3003 | 65.38% / 0.2196 | 100.00% | 0.00% |

HOLDOUT: **17/26** argmax predictions correct; keyword **18/26**. The frozen candidate abstained
on all 26. Accepted accuracy is **undefined**, not 0% or 100%, for every candidate evaluation split.
Every candidate split had 100% abstention. Baselines have full coverage. No task is executed by scoring.

Per-class precision/recall/F1/support, confusion matrices, present-class macro F1, class counts, family
counts, calibration bins and raw/eligible counts are preserved in each `*-metrics.json`; validation
metrics and all threshold/temperature choices are in `freeze.json`. No record text or outputs are copied.

| Protected split | Families | NLL | Brier | ECE (10 bins) |
|---|---:|---:|---:|---:|
| test | 26 | 4.060148 | 0.677229 | 0.103189 |
| adversarial | 7 | 1.393954 | 0.551173 | 0.151258 |
| regression | 8 | 2.068704 | 0.795060 | 0.156824 |
| holdout | 13 | 1.246760 | 0.490750 | 0.237981 |

## 6. Integration gate and authority

**FAILED.** The predeclared gate requires a ≥0.05 improvement over the best TEST baseline for both
accuracy and macro F1, ≥50% TEST/HOLDOUT coverage, ≥90% TEST/HOLDOUT family accuracy, ≥90% selective
accuracy with some coverage on each protected split, a successful validation confidence target, and
complete training capability support. Exact failed clauses are in `gate.json`. High family accuracy
does not compensate for failed capability selection or zero coverage.

No normal-runtime integration or feature flag was added. Existing provider fallback, policy, capability
permissions, hook/approval boundaries and runtime receipts remain untouched. The offline advisor has a
callback seam that falls back on absent, corrupt, unsupported or unknown-capability artifacts and
abstention. A recommendation has no grants and cannot execute. Actual runtime tests confirmed unknown
capability rejection and permission denial without invoking a handler. Existing policy/hook suites pass.
Runtime model-decision receipt linkage is not applicable because integration was withheld. Separate
hash-linked experiment records preserve freeze/model/metric linkage; they are not BLUX execution receipts.

## 7. Artifact and reproduction

The retained model is **345,880 bytes**, outside Git at `/workspace/router-runs/blux-router-0.1/model.json`.
File SHA-256: `0d38269396a29ba7a5c230bd0b447f0318efde74aa558b28902b6b3a097b62ff`.
Canonical payload SHA-256: `a89386182a20a8dca6a2724d33b83d57c8a786132c7a848f15bd6b0689c47541`.
Frozen router-source SHA-256: `6455c7c0d4f49f2b544ec576d2550738c94daecee4b10c0e1d9621ab2f0640c9`.
Training timestamp: `2026-10-27T05:55:17.676112+00:00`.

Artifact metadata includes exact repository/dataset commits and digests, code identity, dependencies,
labels, feature configuration, parameters, calibration, abstention, seed, timestamp and validation
metrics. Protected metrics are checksum-linked external evaluation records and do not rewrite the
frozen artifact after observing evaluation. Distribution policy was written before the first fit.

Two allowed-data reproductions matched every parameter, selection decision and prediction for all
124 train/validation examples. One used a freshly created virtualenv with separately installed pinned
dependencies on the same machine. No protected scoring was repeated. This establishes local clean-venv
reproducibility; it does not claim validation on a newly provisioned cloud machine or Termux.

## 8. Tests, deliberate breakage and limitations

- Training tests: **50 passed**, no skips in the final targeted run.
- Combined BLUX + then-48 training tests: **557 passed, 30 skipped**. Of these, **509** are existing BLUX tests; the two later evidence tests also passed in the final 50-test run.
- cA: **117 passed**; CogA: **343 passed**; dataset suites: **38 passed each** during setup.
- CogA bundled evaluation: **24/24 passed**; BLUX echo round trip passed.
- BLUX/cA/CogA lint, types, generated checks and clean-wheel functional round trips passed.
- Dataset schemas/card/validate/rebuild, lint and typing passed. Both datasets retain one pre-existing formatter failure in `dstool/card.py`; source was not changed.
- cA legacy validator and cA-pro/cA-mini/cpu fixtures passed. CogA legacy verifier/harness passed with its supported `BLUX_COGA_REPO` source override (the installed legacy wheel lacks reasoning packs).
- Five guard-removal mutations were detected: protected fitting, text canary, artifact checksum, aggregate manifest digest, split lock. Each targeted test failed with exit 1; zero mutants survived.
- Portable inference passed with Python `-S`; saved evidence chain and model checksums verified.
- The 30 BLUX skips require unselected external BLUX services, BluxGPT/BLHERMES checkouts, real provider models/credentials or Termux/Bionic. They are not passes. No live model API provider was called.

Main limits: tiny synthetic English dataset; correlated variants within families; missing capabilities;
ambiguous multi-operation records excluded; requested-route labels do not encode validity/refusal;
calibration uncertainty and no operational coverage; no external generalization claim. Dataset-native
contamination checks are not a universal semantic-paraphrase detector. Checksums provide corruption
detection, not a signature or protection against an attacker replacing both artifact and checksum.

## 9. Changes, credits and next capability

The exact 34 changed paths are listed in [FILES.txt](FILES.txt).

Implementation and tests live exclusively under `training/`; `CREDITS.md` adds optional training-tool
licences, source dataset lineage and OpenAI Codex assistance. The previous README reservation is replaced
with the runnable specification. New documents include the artifact policy, audit, this report and
aggregate experiment evidence. Neither datasets nor model coefficients/caches/secrets are committed.
Engine/runtime/SDK/CLI code, dependency manifests/lockfiles and existing tests remain unchanged.

No additional credits repository was edited or message sent. Original dataset authorship/licensing is
retained. No external provider outputs or newly generated training examples were collected. Existing
synthetic-authored manifest provenance is the source of truth; it does not independently prove how
every historical instruction was authored.

**Recommended next step:** a new versioned capability/tool-selection experiment after collecting
approved, diverse instruction-to-route annotations covering every capability and explicit abstention/
out-of-domain cases. Preserve family grouping, reserve genuinely new evaluation families, and compare
against the existing keyword baseline. Investigate family-only routing as a diagnostic, not a release:
its strong scores here do not establish improvement beyond rules. Do not progress to argument prediction,
code generation or reasoning specialization on this failed capability-routing evidence.

## 10. Git completion

The requested commit, push status and final tree status are reported in the completion message after
normal push verification. Source and evidence are committed together, without force updates. The
router-source hash above identifies the trained code without a circular self-referential commit ID.
Exact repository starting commits are in AUDIT.md and freeze.json. Supporting trees stayed clean.
