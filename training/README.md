# BLUX Router 0.1

> **Also in this directory:** `blux_training/`, the trajectory → dataset pipeline (Stage 34): eligibility,
> secret, hidden-reasoning, licence and contamination gates, lock-protected splits, `python -m blux_training`.
> See [blux_training/README.md](blux_training/README.md). It is separate from the Router 0.1 experiment below.

An offline, narrow learned classifier: natural-language task → engine family → capability → confidence
→ route recommendation or **ABSTAIN**. It learns no general reasoning, generates no code, and is not a
foundation model. It cannot certify work, authorize tools, or invoke an engine. Normal BLUX Model API
routing remains unchanged. See [the audit](experiments/AUDIT.md) and [artifact policy](ARTIFACT-POLICY.md).

## Data and labels

Use sibling checkouts of `blux-coga`, `blux-ca`, `blux-coga-dataset`, `blux-ca-dataset`. No dataset files
belong in this repository. Run each dataset's own `dstool validate` and `dstool build --check` with its
pinned engines before training. `router.probe` invokes that toolkit's validation, verifies the manifest's
aggregate record digest, and enforces schema/version, provenance, canaries, locks, family isolation and
known capability IDs. Its audit returns only aggregate metadata and instruction hashes, never protected
text or expected outputs. Exact normalized instruction duplicates across splits additionally fail closed.
Original dataset validators enforce their own exact and near-duplicate full-record controls; this is not
a claim that every possible semantic paraphrase has been excluded.

Train fits the vocabulary, IDF and three classifiers. Validation selects only scalar temperature and the
abstention threshold. Protected `test`, `adversarial`, `regression`, `holdout` never enter fitting,
vocabulary, feature selection, calibration, augmentation or model selection. Every fitting entry point
checks the split and canaries again. The original guarded `training_records` export runs before projecting
records. Only `task.instruction` becomes features. Labels come from input capability calls; a cA record
with multiple distinct capabilities (or none) is excluded and counted. Multiple steps of the same
capability retain that label. Rollback/DAT/recovery commands do not create invented capability labels.

## Candidate and baselines

Measure training-majority and deterministic contract-keyword baselines before fitting. Keyword rules are
fixed from engine capability summaries before protected evaluation, with majority tie-breaking. Their
0.85 pseudo-confidence is a fixed heuristic, not learned calibration. Majority probabilities are training
class frequencies. Both are reported at full coverage, alongside the candidate's full-coverage argmax
accuracy and its abstention-adjusted results.

The candidate uses word unigram/bigram TF-IDF (Unicode word tokens of length ≥2, raw term count,
smoothed IDF, L2 norm), L2 logistic regression with `C=4`, balanced class weights, lbfgs, at most 2,000
iterations, seed 1729, one numerical thread. One family head and two conditional capability heads yield
joint probabilities. Training's family head is binary; export its two logits as ±half of sklearn's
binary logit so softmax reproduces the sigmoid. No transformer or pretrained weights are downloaded.
This is an inspectable CPU baseline appropriate to fewer than 100 training records, not evidence of
open-domain language understanding.

Freeze the best joint-NLL temperature in `[0.5, 0.75, 1, 1.5, 2]` using validation only. From thresholds
`[0, .4, .5, .6, .7, .8, .9, .95, 1]`, choose maximum validation coverage with at least 8 accepted
examples and ≥90% accuracy; otherwise use 1 (abstain). An empty feature vector always abstains. No
post-holdout tuning. Calibration on tiny, family-correlated validation data has high uncertainty.

## Reproduce

Python 3.12 is the reference platform. Use the existing cloud checkout; no extra worktree is required.
The environment setup provides `/workspace/setup/dev` for editable BLUX/SDK/cA/CogA development and
separate `/workspace/setup/blux-{ca,coga}-dataset` environments containing their pinned engines.
For another workspace, create the same `setup/` layout under that workspace and pass `--workspace`.

```bash
cd /workspace/blux
source /workspace/setup/dev/bin/activate
python -m pip install -r training/requirements.txt
python -m pytest training/tests -q
python -m training.run_mutations
python -m training.router.experiment prepare --out /workspace/router-runs/blux-router-0.1
python -m training.router.experiment reproduce --out /workspace/router-runs/blux-router-0.1
# Only after inspecting freeze.json; this reserves a one-shot protected evaluation:
python -m training.router.experiment evaluate --out /workspace/router-runs/blux-router-0.1
```

An existing prepare/evaluation reservation refuses a second run. Never delete a reservation to retry
an observed evaluation. Use `reproduce` for identical parameters and predictions on allowed data, without
reopening protected splits. Training parameter determinism excludes timestamps/provenance packaging;
the full artifact hash intentionally includes them. Frozen code and dependency versions, dataset commits
and digests must match; later BLUX result/documentation commits may descend from the recorded base.
Code identity is a SHA-256 of the router source files, avoiding a circular self-referential commit ID.
New clean environments must install all pinned dependencies and sibling engines first.

## Protected evaluation and acceptance

Before accessing protected examples, persist `freeze.json`, artifact checksums, dependencies, lineage,
validation metrics, baselines, selection decisions and gate configuration. Evaluation runs TEST once,
then ADVERSARIAL, then REGRESSION, and HOLDOUT last. Every split reports original and eligible counts,
family count, accuracy, all-14-class macro F1 (absent classes count zero), present-class macro F1,
per-class precision/recall/F1, confusion matrix, family/capability accuracy, coverage, abstention,
accepted accuracy, NLL, multiclass Brier and ten-bin ECE. Small correlated families and single-label
exclusions limit interpretation. Adversarial routing accuracy measures the **requested capability**,
not whether the engine should accept the request. It is not an execution-safety score.

Every integration-gate clause is required:

- TEST accuracy and macro F1 each exceed the best baseline by at least 0.05 absolute.
- TEST and HOLDOUT coverage ≥50%, family accuracy ≥90%.
- Each protected split has at least one accepted route with ≥90% accepted accuracy.
- Validation meets its threshold target.
- Every authoritative capability has training support.

Failure closes experiment 0.1 without normal-runtime integration. Even a passing result would require a
separate explicit experimental feature flag and runtime policy/provenance checks before enabling it.
No such feature flag is installed by this offline experiment. The advisor's callback seam demonstrates
safe delegation to the caller's existing provider path on absence, malformed/corrupt/unsupported artifact,
unknown capability or low confidence. It is not a replacement provider and cannot construct engine arguments.

## Artifact, provenance and authority

`model.json` is a size-bounded canonical JSON envelope with `payload` and SHA-256; it holds model identity,
version, type, labels, unsupported capabilities, feature configuration, vocabulary/IDF, weights/bias,
temperature, threshold, seed, dependency versions, timestamp, exact repository lineage, dataset digests,
code hash, selection history and validation metrics. The frozen evaluation and hash-linked experiment
receipts reference the artifact checksum without rewriting its parameters or checksum after evaluation.
The metrics files therefore complete the artifact's evaluation provenance. These are experiment records,
not forged BLUX runtime execution receipts. No model calls, secrets, private data, hidden chain-of-thought,
or Claude/Anthropic training examples were added. JSON checksums detect corruption, not malicious forgery;
a future distribution must authenticate a checksum separately. No pickle is loaded.

Portable inference uses only Python's standard library:

```bash
python -m training.router.predict --artifact /workspace/router-runs/blux-router-0.1/model.json \
  --capabilities /workspace/router-runs/blux-router-0.1/capabilities.json \
  --task 'Plan work with dependencies and two workers'
```

A recommendation is advisory-only and `certified: false`. BLUX's capability registry, default-deny policy,
permission grants, hook/approval boundaries, provider fallback and receipt handling remain authoritative.
CogA retains reasoning authority; cA never certifies; DAT retains independent validation authority.
Training dependencies are optional tooling and are never added to runtime package dependencies.

Tests cover split isolation, vocabulary isolation, canaries, provenance, labels, integrity/missing datasets,
serialization, checksums, repeatability, abstention, artifact failure fallback, unknown capabilities and
real runtime permission denial. `run_mutations` removes five safety guards in throwaway temporary copies
and requires the corresponding tests to fail. See experiment results for exact executed counts and limits.
