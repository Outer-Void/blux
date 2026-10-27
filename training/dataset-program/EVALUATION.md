# Independent evaluation and native-model promotion

Freeze task families, meaningful baselines, metrics, sample-size/uncertainty policy, stopping
rule, acceptance thresholds and authority-boundary tests before protected evaluation access.
Appoint an independent evaluation custodian. Authors of training templates do not prepare
protected siblings. Protect all variants/translations/repository forks as a family unit.

Split assignment is immutable at family level. Preserve source/content digests, canaries,
contamination audit and access logs. Locked test/holdout content never enters training,
validation, prompts used for training generation, or iterative manual tuning. A consumed
holdout is diagnostic history, not fresh independent evidence; replace it under a new protocol.
Preflight exact checks are necessary but insufficient: semantic/near-deduplication and a
protected-set overlap audit must pass before fitting.

Compare a small eligible base, any domain-adapted candidate, an external/general baseline and
existing deterministic specialists on their intended work. Measure correctness, tool-use
validity, authority preservation, latency, memory and artifact reproducibility. Track failures.
Promotion requires independent improvement, acceptable regressions/uncertainty, unchanged
DAT/device/business/care authority, hashes/reproducible artifacts and a tested stable logical
model ID fallback/rollback. No promotion from Router-only accuracy.

Status: protocol drafted; custodian, independent items, final thresholds, compute budget,
baselines, serious training, protected evaluation and rollback exercise NOT COMPLETE.
