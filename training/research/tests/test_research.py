"""Safety/integrity controls for development research; no real protected scoring."""

from __future__ import annotations

import pytest
from training.research.crossval import (
    family_majority,
    fold_plan,
    paired_family_bootstrap,
    protocol,
    subset_families,
    validate_plan,
)
from training.research.ledger import append, load, verify
from training.research.quality import auc, random_row_fold_risk


def rows():
    return [
        {
            "id": str(n),
            "split": "train" if n < 6 else "validation",
            "label": "reason.plan" if n % 2 else "artifact.modify",
            "semantic_family": "f" + str(n // 2),
        }
        for n in range(12)
    ]


def test_grouped_folds_partition_families():
    config = dict(protocol(), n_splits=3)
    plan, _ = fold_plan(rows(), config)
    validate_plan(rows(), plan)
    assert len(plan) == 3


def test_research_never_resplits_protected():
    data = rows()
    data[0]["split"] = "holdout"
    with pytest.raises(ValueError, match="protected"):
        fold_plan(data, dict(protocol(), n_splits=3))


def test_family_leak_rejected():
    data = rows()
    held = [[0, 2, 4, 6], [1, 3, 8, 10], [5, 7, 9, 11]]
    plan = [{"train": [i for i in range(12) if i not in vv], "validation": vv} for vv in held]
    with pytest.raises(ValueError, match="family leakage"):
        validate_plan(data, plan)


def test_out_of_fold_missing_rows_rejected():
    data = [{"semantic_family": str(n)} for n in range(3)]
    with pytest.raises(ValueError, match="every eligible"):
        validate_plan(data, [{"train": [1, 2], "validation": [0]}])


def test_grouped_learning_curve_does_not_select_variants():
    data = rows()
    subset = subset_families(data, 0.5, 1729)
    chosen = {r["semantic_family"] for r in subset}
    assert len(subset) == 2 * len(chosen)
    assert {r["label"] for r in subset} == {r["label"] for r in data}


def test_family_majority_is_not_capability_majority():
    data = [{"label": "reason.plan"}] * 3 + [{"label": "artifact.modify"}] * 2 + [{"label": "code.patch"}] * 2
    result = family_majority(data, 1)[0]
    assert result["prediction"] == "ca"
    assert result["probabilities"]["ca"] == 4 / 7


def test_paired_family_bootstrap_determinism():
    data = rows()
    perfect = [{"prediction": r["label"]} for r in data]
    wrong = [{"prediction": "ABSTAIN"} for _ in data]
    result = paired_family_bootstrap(data, perfect, wrong, protocol())
    assert result["percentile_interval"] == [1, 1]
    assert result == paired_family_bootstrap(data, perfect, wrong, protocol())


def test_ood_auc_requires_negative_and_positive_evidence():
    assert auc([], [1]) is None
    assert auc([0.9], [0.1]) == 1
    assert auc([0.1], [0.9]) == 0


def test_naive_fold_risk_does_not_fit_leaky_models():
    result = random_row_fold_risk(rows(), 3, 1729)
    assert result["models_trained_on_leaky_splits"] == 0
    assert result["families_spanning_row_folds"] > 0


def test_ledger_identity_and_protected_reuse(tmp_path):
    path = tmp_path / "ledger.jsonl"
    first = {"id": "one", "protected_evidence_consumed": ["old-set"]}
    append(path, first)
    with pytest.raises(ValueError, match="identity"):
        append(path, first)
    record = load(path)[0]
    second = {
        "experiment": {"id": "two", "protected_evidence_consumed": ["old-set"]},
        "previous_sha256": __import__("training.router.common", fromlist=["digest"]).digest(record),
    }
    with pytest.raises(ValueError, match="historical"):
        verify([record, second])


def test_ledger_checksum_chain(tmp_path):
    path = tmp_path / "ledger.jsonl"
    append(path, {"id": "one", "protected_evidence_consumed": []})
    append(path, {"id": "two", "protected_evidence_consumed": []})
    records = load(path)
    records[0]["experiment"]["id"] = "tampered"
    with pytest.raises(ValueError, match="tampering"):
        verify(records)


def test_ledger_refuses_to_persist_reused_protected_data(tmp_path):
    path = tmp_path / "ledger.jsonl"
    append(path, {"id": "one", "protected_evidence_consumed": ["old-set"]})
    previous = path.read_bytes()
    with pytest.raises(ValueError, match="historical"):
        append(path, {"id": "two", "protected_evidence_consumed": ["old-set"]})
    assert path.read_bytes() == previous


def test_ledger_duplicate_identity_without_protected_data(tmp_path):
    path = tmp_path / "ledger.jsonl"
    first = {"id": "one", "protected_evidence_consumed": []}
    append(path, first)
    with pytest.raises(ValueError, match="identity"):
        append(path, first)


@pytest.mark.parametrize("module_name", ["training.error_proxy", "training.family_diagnostics"])
def test_new_development_diagnostics_reject_protected(module_name):
    import importlib

    module = importlib.import_module(module_name)
    with pytest.raises(ValueError, match="protected data"):
        module.assert_development_records([{"split": "holdout"}])


def test_error_proxy_metrics_do_not_claim_ood():
    from training.error_proxy import binary_metrics

    result = binary_metrics([True, False], [0.9, 0.1], 0.5)
    assert result["recall"] == 1
    assert result["precision"] == 1
    assert result["coverage"] == 0.5
    assert "neither an OOD" in result["interpretation"]
    rejected = binary_metrics([True, False], [1, 1], 0.5)
    assert rejected["source_engine_success_given_accepted"] is None


def test_direct_family_baseline_no_training():
    from training.family_diagnostics import rule_predictions

    summaries = {"artifact.modify": "edit code file", "reason.plan": "plan dependencies tasks"}
    result = rule_predictions([{"text": "edit code file"}], "contract-overlap", summaries)
    assert result[0]["prediction"] == "ca"
    assert sum(result[0]["probabilities"].values()) == 1


def test_hybrid_family_filter_does_not_create_capabilities():
    from training.hybrid_diagnostics import restrict_capabilities

    probs = {"reason.plan": 0.2, "reason.analyze": 0.3, "artifact.modify": 0.5}
    assert restrict_capabilities(probs, "coga") == {"reason.plan": 0.4, "reason.analyze": 0.6}
    assert restrict_capabilities(probs, "ABSTAIN") == {}
