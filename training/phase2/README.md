# Router 0.2 — coverage, diversity and abstention

**Status: BLOCKED_DATA. No promotion candidate is frozen. No protected evaluation ran.**

The owner chose existing eligible records with documented gaps. The Phase-1 engine-regression datasets
are unchanged. Router 0.2 inherits only their original train/validation records; no model-generated
natural-language corpus, new labels for source records, private data or provider output was added.
Phase 1 code, records and artifact remain historical evidence. Its protected scoring was not repeated.

## Dataset surface

`blux-routing-0.2` is a distinct compiled dataset outside Git, by default at
`/workspace/router-runs/blux-router-0.2/dataset`. It contains 90 original single-label train examples
(30 semantic families), 34 validation examples (17 families), and zero fresh protected examples.
Three multi-capability source train records are excluded. Each compiled record includes the source
repository, exact commit, split path/record ID, content digest, original authors/licence, inherited
approval, semantic family and declared routing label. It does not reinterpret engine refusal outputs.
Source text is materialized only outside the checkout. Source datasets are referenced, never copied into
BLUX. Source labels describe the sole distinct requested capability, not execution validity or safety.

The dataset schema supports owner-authored records, explicit `ABSTAIN`, reasons (out-of-domain,
unsupported, incomplete, ambiguous, multi-operation), short/verbose/paraphrase/adversarial/misleading-keyword
phenomena and semantic/template/paraphrase families. This support is not a claim that the existing corpus
contains those examples. New source-record examples cannot become fresh protected families.

Builds are deterministic, have file/content digests and immutable split locks, refuse output overwrites,
check exact and four-word-shingle near-duplicates (Jaccard ≥0.8), and quarantine detectable reuse of
Phase-1 protected text. Quarantine fingerprints are calculated mechanically without manually displaying
protected text or expected outputs. Protected canaries are rejected anywhere in a new record. Family
labels are declarations, not an automatic proof of semantic independence; owner review remains necessary.

## Owner authoring tools and requirements

`schema.record_schema` emits a schema derived from current authoritative capabilities.
`authoring.owner_seeds` requires a matching seed-file checksum, named reviewer, explicit training rights
and consistent per-record provenance. This checks attestation consistency, not a reviewer's legal identity.
`authoring.expand_template` consumes only caller-supplied template/slot strings; it supplies no Codex prose.
All variants keep the same semantic/template/paraphrase family and split. Variants never count as newly
independent families. Test-only synthetic fixtures are rejected by production corpus validation.

Collection targets were declared before development comparisons:

| Split | Distinct families per capability | Distinct ABSTAIN families per reason |
|---|---:|---:|
| train | 25 | 10 |
| validation | 5 | 3 |
| test | 5 | 3 |
| adversarial | 2 | 3 |
| regression | 2 | 3 |
| holdout | 5 | 3 |

All 14 current capabilities must be represented. No existing single-label train examples cover
`artifact.validate` or `repo.patch`; validation additionally lacks `reason.rank`. There are zero
rights-cleared explicit abstention/OOD examples. Fresh protected families are absent. The detailed
class/split/family inventory is in `evidence/coverage.json`.

New rights-cleared owner seed families, rather than lexical substitutions, are required to satisfy these
targets. Current deficits total 320 capability + 50 abstention training families, 53 capability + 15
abstention validation families, and 256 fresh protected capability/abstention families. Each new example
needs its correct label/reason, semantic family, template/paraphrase linkage if applicable, split,
phenomena, authors, source, rights and reviewer approval. Protected examples must be authored/reviewed
outside model development; their text must not be shown to the developer for feature design.

## Development comparisons

Three fixed hypotheses were specified in `protocol.py` before fitting:

- word-only hierarchical logistic model (the Phase-1 architecture retrained solely on eligible data);
- direct word+character logistic model with threshold-only abstention;
- direct word+character model with explicit learned abstention, requiring negative examples.

The third is **NOT_TRAINED** because its required data is absent. It is exercised only by explicitly
marked synthetic unit-test fixtures, which do not enter the real experiment. Model features use only
instructions. Word blocks use unigrams/bigrams; character blocks use 3–5 grams. Each block is TF-IDF/L2
normalized, then equally weighted. Hyperparameters remain C=4, class-balanced lbfgs, seed 1729, 2,000
maximum iterations, one numerical thread. Native exported features are checked against sklearn.

Temperature and threshold grids use validation only. Unlike Phase 1, safety targets explicitly require
observable abstention recall/precision. No negative examples means those measures are undefined and the
calibration target cannot pass; the threshold becomes 1. This records a data gap rather than inventing
an OOD success. All forced/argmax scores are explicitly diagnostic.

The unchanged Phase-1 keyword baseline remains a full-coverage competitor. Majority, a separate
high-margin deterministic fallback, family-only classifier, deterministic keyword-family aggregation,
and a deterministic→learned→abstain hybrid are also measured. The keyword competitor is not weakened to
make the fallback/hybrid look better. High-margin rules require at least two cue matches and a score gap
of two; these criteria were fixed before comparisons.

## Protocol and artifacts

Development data is not fresh protected evidence. None of TEST, ADVERSARIAL, REGRESSION or HOLDOUT was
scored in Router 0.2. Their record counts are zero. `evaluate` refuses absent corpus prerequisites and
then refuses the lack of a frozen promotion candidate; this development-only command cannot promote a
model. Once new seeds exist, a separately reviewed candidate-freeze/protected-evaluation step is still
required. This implementation does not claim completion of that blocked experimental stage.

The predeclared gate additionally requires a 0.05 improvement over majority TEST accuracy and keyword
TEST authoritative-class macro F1, ≥50% TEST/HOLDOUT coverage, ≥95% selective accuracy on protected
splits, ≥90% abstention precision/recall, ≥95% OOD recall and TEST/HOLDOUT family accuracy, clean lineage,
reproduction, portable validation and unchanged authority boundaries. This gate is recorded, not relaxed.

A development-only `blux-router-0.2` JSON artifact is retained outside Git. It records source commits,
compiled dataset digest, code identity, architecture, feature blocks, coefficients, labels, training
counts, calibration, seed, timestamp, environment and dependency versions. Aggregate evidence references
its checksum and does not commit coefficients. It cannot be loaded for normal routing: default loading
and fallback deny development artifacts. Explicit offline development opt-in is required even for
inspection. Runtime integration is absent. BLUX policy, permissions, approvals/hooks, CogA/cA/DAT and
receipts remain authoritative.

`metrics.py` reports final-decision accuracy separately from argmax accuracy, authoritative 14-class and
15-outcome macro F1, present-class macro F1, per-class raw counts/confusion, NLL/Brier/ECE, coverage,
selective accuracy, abstention precision/recall/AUC and per-reason behavior. A missing denominator yields
null, never a fabricated PASS. Calibration bins assess argmax confidence; outcome F1 assesses the actual
abstention-adjusted decision. Threshold-only probabilities do not include a learned abstention mass.

## Commands

Run from the existing BLUX checkout with the development environment and sibling dataset environments:

```bash
source /workspace/setup/dev/bin/activate
cd /workspace/blux
python -m pytest training/tests training/phase2/tests -q
python -m training.run_phase2_mutations
python -m training.phase2.experiment develop       # one reserved development run; raw data outside Git
python -m training.phase2.experiment reproduce     # only allowed data, no protected scoring
python -m training.phase2.experiment verify
python -m training.phase2.authoring --capabilities /workspace/router-runs/blux-router-0.2/capabilities.json \
  --schema-output /workspace/router-runs/blux-router-0.2/owner-seed.schema.json
```

`develop` refuses to overwrite an existing run. Reproduction compares all model parameters and aggregate
development metrics without treating calibration metadata/timestamps as model improvements. A fresh venv
on this same machine checks dependency reproduction; no Termux, GPU or fresh-cloud portability is claimed.
See the program report and the aggregate files in `evidence/` for executed results and exact test counts.
