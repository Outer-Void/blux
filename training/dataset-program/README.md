# BLUX Dataset Program v1 — acquisition and evaluation controls

Router 0.x remains experimental historical evidence. Do not produce Router 0.3 as a substitute
for general intelligence. Existing CogA/cA datasets remain specialist/regression material.
`families.json` covers the requested corpus families; no bulk corpus or native model is claimed.

The existing `blux_training` pipeline remains authoritative for trajectory eligibility,
secret/PII checks, normalization and receipt provenance. This program adds corpus-source
planning and a family/split/digest preflight; it does not replace those gates.

Before acquisition: pin source revision, retain license and permission evidence, classify
source/public status, and clear source eligibility. Private user/project/participant data and
provider hidden reasoning are prohibited. Public visibility alone is not training permission.
Each accepted document needs a family identity linking templates, repository forks, versions,
translations and other siblings. Exact SHA-256 dedupe is required before downstream near-dedupe.
Use corpus-scale shingling/MinHash and audit false positives before substantial ingestion.

`python tools/preflight.py records.jsonl --lock previous-lock.json --out next-lock.json`
checks family locks, exact duplicate content, eligibility and digests. The lock is written
atomically only after all records pass. It is a preflight, not proof of near-duplicate or
semantic contamination clearance. Do not use it alone to declare training eligible.
`python -m unittest discover -s tests -v` tests rejection and immutability behavior.

Independent protected evaluation policy: `EVALUATION.md`; model/compute shortlist: `BASE_MODELS.md`.
No protected evaluation content is embedded here. It must be independently authored and
access-controlled before serious fitting. No training run, baseline result or promotion is fabricated.
