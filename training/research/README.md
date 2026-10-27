# Development research after Router 0.2

No model was integrated. All research here uses only the 124 eligible source train/validation records
in the distinct Router 0.2 dataset, grouped into 47 declared semantic families. The four Phase-1
protected splits remain historical evidence. Zero new protected evaluations were performed.

The source dataset is too small and incomplete to finish Router 0.2's promotion experiment. The owner
explicitly chose existing eligible records with documented gaps; no Codex natural-language corpus was
created. See [Phase 2](../phase2/README.md) for source provenance, schemas, validators and authoring tools.
All weights, raw routing records, prediction caches and frozen execution sources are outside Git under
`/workspace/router-runs/`. The [artifact policy](../ARTIFACT-POLICY.md) still applies.

`protocol.json` was saved before the grouped fits. Five StratifiedGroupKFold development folds use source
semantic families, seed 1729 and fixed C=4 balanced logistic models. Word, character and combined features
share cached fold feature matrices across capability/family objectives. Native features are checked against
sklearn. Forty heads include 30 family/capability comparisons and 10 fixed family learning-curve fits.
Every record receives exactly one out-of-fold prediction, and no family spans training/validation.
The warning that one class has only three records is retained; `reason.rank` has no training family in
one fold, and `artifact.validate`/`repo.patch` have none in every fold. All 14 capabilities remain in metrics.

This is development cross-validation, not fresh TEST/HOLDOUT evidence. It reassigns eligible development
records to temporary development folds without changing source/compiled split locks. No calibration or
OOD threshold is fitted in the grouped comparisons. Raw confidence is diagnostic, not a safety guarantee.
Semantic-family bootstrap intervals are descriptive after comparisons, not independent promotion tests.
The word family head is the same binary logistic family submodel as the hierarchical reference; it is not
redundantly retrained under a new name.

`quality.py` quantifies family counts, class imbalance, potential naive-row-split leakage, source engine
errors, contract cue overlap, development confusions, lexical distance and confidence errors. No leaky
model is trained. AUC for in-domain error ranking is explicitly not an OOD AUC.

Additional uniquely identified development diagnostics are in `../family_diagnostics.py`,
`../error_proxy.py` and `../hybrid_diagnostics.py`. Direct family rules derive from contract summaries
or unchanged keyword cues. Engine-error proxy targets are mechanically derived from eligible source
expected metadata; they do not invent OOD or safe-routing labels. Eight errors span only three families.
The hybrid study recombines existing OOF predictions without further fitting. These follow-up comparisons
are explicitly post-hoc development analyses, not frozen protected confirmations.

The append-only `experiments.jsonl` preserves Phase 1 and closes each new identity once. It records
questions, hypotheses, eligible data, baselines, candidates, protocols, consumed protected identities,
results, gate verdicts, limitations and next hypotheses. Duplicate identities, broken hash chains and
reconsumption of protected identifiers fail before a ledger append is persisted. Use one ledger writer;
this is not a distributed experiment scheduler or a signature system.

## Reproduce retained runs

```bash
source /workspace/setup/dev/bin/activate
cd /workspace/blux
python -m pytest training/research/tests -q
python -m training.run_research_mutations
python -m training.research.crossval --reproduce
python -m training.research.quality
python -m training.hybrid_diagnostics --reproduce
# The following exact frozen sources preserve their original pre-formatting source checksum:
PYTHONPATH=/workspace/blux python /workspace/router-runs/blux-family-baselines-0.1/frozen-source.py --reproduce
PYTHONPATH=/workspace/blux python /workspace/router-runs/blux-engine-error-proxy-0.1/frozen-source.py --reproduce
```

Two long hypothesis literals were wrapped after those development runs. Original checksum-verified source
snapshots were retained before formatting; parsed executable ASTs were verified identical. The immutable
preregistrations/results were not rewritten. `source-formatting-transition.json` records original and
repository source hashes plus AST identity. Reproduction used the original frozen entrypoints in the fresh
same-machine virtualenv. Current formatted modules are suitable for new, separately identified experiments;
the exact old runs require the retained snapshots. Raw/model artifacts and these snapshots have not been
published, so this is not a claim of artifact restoration from a fresh Git clone or a new cloud machine.

`evidence/` contains aggregate metrics, protocols, reproduction and mutation evidence, source transitions
and artifact checksums; coefficients are excluded. See the wake-up report for exact results, limits,
validation counts and owner-data requirements. No Termux, GPU or independent real-world generalization
claim is made. Further promotion work requires rights-cleared, owner-reviewed negative examples,
missing capability families, and completely fresh protected data. Extra model complexity cannot supply
that missing evidence.
