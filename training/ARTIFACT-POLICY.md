# Router artifact policy

Before the first fit: coefficients, vocabulary, model caches, and raw examples remain outside Git,
by default under `/workspace/router-runs/`. Commit code, protocols, checksums, aggregate metrics,
and experiment receipts only. Do not copy sibling datasets. No public artifact distribution is
approved by this experiment. Dataset and BLUX proprietary licences continue to apply.

A JSON artifact is data, never pickle or executable code. Its SHA-256 envelope detects accidental
corruption, not malicious replacement: a distributor must separately authenticate the published
checksum. Load only a trusted, size-bounded local file, check its checksum and version, and compare
all labels against the caller's authoritative capability allowlist. Invalid or unavailable models
abstain. Inference recommends a capability; it never executes or authorizes it.

Experiment 0.1 is closed after its first protected evaluation. Store the freeze file before opening
TEST. Read TEST, ADVERSARIAL, REGRESSION, then HOLDOUT last. Do not retry an existing experiment's
protected evaluation; a failed/crashed attempt leaves a reservation and requires investigation.
Reproduction compares training parameters and portable predictions without reopening protected
splits. A new model design after holdout requires a new experiment identity and disclosure that
these small public evaluation sets have already been observed.
