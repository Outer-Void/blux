# blux_training: the BLUX trajectory → dataset pipeline

`blux_training` turns BLUX trajectories into versioned training/evaluation datasets, and refuses anything it
may not keep. It ships in the `outervoid-blux` distribution (`python -m blux_training`). Engine regression
datasets stay in `blux-ca-dataset` and `blux-coga-dataset`; this pipeline protects their evaluation splits
and never copies their data.

## Flow

```
trajectory source (schemas/source.v1)
 -> eligibility -> hidden-reasoning check -> secret check -> licence check -> receipt validation
 -> personal-data redaction -> normalization -> canonical record (schemas/record.v1)
 -> quality score -> exact and near-duplicate removal -> contamination check against protected sets
 -> split assignment (lock-protected) -> dataset: splits/*.jsonl, manifest.json, report.json, split-lock.json
```

## Policy (fixed in code; an input cannot loosen it)

| Gate | Rule | Outcome |
|---|---|---|
| Eligibility | Synthetic sources declare `basis=synthetic`, `data_class=synthetic`. BLUX operations need an owner approval (`approved_by`, `approved_at`), public data and their receipts. **Private user or project data is never training data, even when approved.** | reject |
| Hidden reasoning | Any field named like model reasoning (`chain_of_thought`, `scratchpad`, `thinking`, `reasoning`, `reasoning_details`, `thought_process`, …) or a reasoning marker (`<think>` as Qwen3 and DeepSeek-R1 emit it, `<thinking>`, a harmony `analysis` channel). Only explicit BLUX structures (`reasoning_structure`, CogA's `reflection`) are kept. | reject |
| Secrets | Credentials and secret-named values anywhere (BLUX's `redact()` patterns) | reject (never partly redacted) |
| Personal data | E-mail outside the reserved example domains (and their subdomains), phone numbers with separators or a `+` country code, non-documentation IPv4 and IPv6, home paths (`/home`, `/Users`, `/root`, Termux, `C:\Users`), Luhn-valid card numbers, US SSNs, terms from `--private-terms`. Bare digit runs are not treated as phone numbers | redacted; `privacy_class: redacted` |
| Licences | At least one allowed licence; every constructed artifact has one | reject |
| Quality | Overall score below 0.5 (completeness, validation results, repair iterations) | reject |
| Duplicates | Same content hash, or 3-gram Jaccard ≥ 0.9 with a kept record | reject |
| Contamination | ≥ 80% of a record's input appears in one protected evaluation item: `--protected` files, the previous dataset's test/holdout/regression splits (found next to `--lock`), or a protected record of the same build | reject |
| Splits | Hash-based train/validation/test. A `split_hint` can only protect (holdout, test, regression). A record in the lock never moves. | assign |

Reports name the rule and the location, never the offending value.

## Commands

```sh
python -m blux_training build training/examples/sources --out build/dataset --version 2026.10.27 \
    [--lock previous/split-lock.json] [--protected ../blux-coga-dataset/data/holdout.jsonl ...] \
    [--private-terms ~/.config/blux/private-terms]      # untracked; one term per line
python -m blux_training validate build/dataset           # re-checks digests, schema, splits, secrets, reasoning, PII
make training-example                                    # the example above, end to end
```

Builds are deterministic: the same inputs, lock and version give byte-identical outputs. `validate` re-checks
a dataset from its files alone. Tests: `tests/training/test_pipeline.py` (CI runs them with the rest of the
suite).

## Record fields

`record_id`, `schema_version`, `source_type`, `source_project`, `operation_id`, `parent_operation_id`, `input`,
`intent`, `context_summary`, `reasoning_structure`, `planned_actions`, `tool_calls`, `tool_results`,
`constructed_artifacts`, `validation_results`, `repair_iterations`, `final_result`, `accept_reject`,
`quality_scores`, `safety_class`, `privacy_class`, `provenance` (source hash, receipt ids, eligibility,
pipeline version, redactions), `licenses`, `hashes` (content and record SHA-256), `dataset_split`,
`model_versions`, `engine_versions`. Schema: `blux_training/schemas/record.v1.schema.json`.

## Not yet

No exporter reads trajectories out of a BLUX System store automatically; sources are written explicitly.
That keeps "nothing is training data by default" true. An approved export command is future work.
