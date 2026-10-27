"""Source-engine-error proxy from eligible expected metadata; explicitly not OOD ground truth."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import warnings
from collections import Counter
from pathlib import Path

from training.phase2.dataset import load_verified
from training.phase2.models import predict, vector
from training.research.crossval import parity, prepare_features, train_head
from training.router.common import digest, read, write

ERROR, SUCCESS = "ENGINE_ERROR", "NO_ENGINE_ERROR"
CONFIG = {
    "id": "blux-engine-error-proxy-0.1",
    "hypothesis": (
        "existing engine errors may support a rejection-risk proxy w"
        "ithout generating prose; this cannot establish OOD behavior"
    ),
    "n_splits": 3,
    "seed": 1729,
    "representations": ["word", "word-char"],
    "thresholds": [0.5, 0.7, 0.9],
    "rule_tokens": ["invalid", "malformed", "refuse", "incomplete", "missing", "corrupt"],
    "promotion_eligible": False,
    "protected_evidence_consumed": [],
}


def error_labels(rows: list[dict], workspace: Path) -> dict[str, bool]:
    source = {}
    for name in ("blux-ca-dataset", "blux-coga-dataset"):
        manifest = read(workspace / name / "data/manifest.json")
        for split in ("train", "validation"):
            path = workspace / name / f"data/{split}.jsonl"
            if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["files"][f"data/{split}.jsonl"]["sha256"]:
                raise ValueError("source file changed before error-proxy derivation")
            for line in path.read_text().splitlines():
                r = json.loads(line)
                source[name + ":" + r["record_id"]] = r["expected"].get("kind") == "error" or any(
                    s["kind"] == "error" for s in r["expected"].get("steps", [])
                )
    if set(r["id"] for r in rows) - set(source):
        raise ValueError("unverified source record in error proxy")
    return {r["id"]: source[r["id"]] for r in rows}


def binary_metrics(truth: list[bool], scores: list[float], threshold: float) -> dict:
    predicted = [p >= threshold for p in scores]
    tp = sum(t and p for t, p in zip(truth, predicted, strict=True))
    fp = sum(not t and p for t, p in zip(truth, predicted, strict=True))
    fn = sum(t and not p for t, p in zip(truth, predicted, strict=True))
    accepted = sum(not p for p in predicted)
    false_accepts = fn
    n = len(truth)
    return {
        "n": n,
        "engine_error_truth_count": sum(truth),
        "engine_success_truth_count": n - sum(truth),
        "threshold": threshold,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "coverage": accepted / n,
        "source_engine_success_given_accepted": (accepted - false_accepts) / accepted if accepted else None,
        "false_accepts": false_accepts,
        "accuracy": sum(t == p for t, p in zip(truth, predicted, strict=True)) / n,
        "nll": -sum(math.log(max(1e-15, p if t else 1 - p)) for t, p in zip(truth, scores, strict=True)) / n,
        "brier": sum((p - int(t)) ** 2 for t, p in zip(truth, scores, strict=True)) / n,
        "interpretation": "engine-error proxy only; this is neither an OOD nor an authorization metric",
    }


def assert_development_records(rows: list[dict]) -> None:
    if any(r["split"] not in ("train", "validation") for r in rows):
        raise ValueError("protected data cannot enter a development-only diagnostic")


def run(data: Path, workspace: Path, out: Path, reproduce: bool = False) -> None:
    from sklearn.model_selection import StratifiedGroupKFold

    artifact = read(data / "model.json")["payload"]
    labels = artifact["authoritative_capabilities"]
    rows, manifest = load_verified(
        data / "dataset",
        artifact["dataset_sha256"],
        labels,
        read(data / "canaries.json"),
        read(data / "quarantine.json"),
    )
    assert_development_records(rows)
    error = error_labels(rows, workspace)
    truth = [error[r["id"]] for r in rows]
    if len({r["semantic_family"] for r in rows if error[r["id"]]}) < CONFIG["n_splits"]:
        raise ValueError("too few independent engine-error families for preregistered proxy experiment")
    splitter = StratifiedGroupKFold(n_splits=CONFIG["n_splits"], shuffle=True, random_state=CONFIG["seed"])
    with warnings.catch_warnings(record=True) as captured:
        plan = [
            {"train": a.tolist(), "validation": b.tolist()}
            for a, b in splitter.split(list(range(len(rows))), truth, [r["semantic_family"] for r in rows])
        ]
    from training.research.crossval import source_code_sha256

    prereg = {
        "shared_code_sha256": source_code_sha256(),
        "source_commits": artifact["source_commits"],
        "dependencies": {
            n: importlib.metadata.version(n) for n in ("scikit-learn", "numpy", "scipy", "joblib", "threadpoolctl")
        },
        "python": platform.python_version(),
        "config": CONFIG,
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "dataset_sha256": digest(manifest),
        "label_sha256": digest(error),
        "folds": plan,
        "warnings": [str(w.message) for w in captured],
    }
    if not reproduce:
        out.mkdir(parents=True, exist_ok=True)
        with (out / "experiment.started").open("x") as f:
            f.write(CONFIG["id"])
        write(out / "preregistered.json", prereg)
    elif read(out / "preregistered.json") != prereg:
        raise ValueError("engine-error proxy reproduction inputs changed")
    outputs = {r: [None] * len(rows) for r in ["majority", "rule", *CONFIG["representations"]]}
    parameters, folds = [], []
    for number, fold in enumerate(plan):
        training = [rows[i] for i in fold["train"]]
        validation = [rows[i] for i in fold["validation"]]
        if {r["semantic_family"] for r in training} & {r["semantic_family"] for r in validation}:
            raise ValueError("proxy family leakage")
        targets = [ERROR if error[r["id"]] else SUCCESS for r in training]
        counts = Counter(targets)
        if not counts[ERROR] or not counts[SUCCESS]:
            raise ValueError("proxy fold lacks both source outcomes")
        majority = counts[ERROR] / len(training)
        folds.append(
            {
                "fold": number,
                "train_n": len(training),
                "validation_n": len(validation),
                "train_error_n": counts[ERROR],
                "validation_error_n": sum(error[r["id"]] for r in validation),
            }
        )
        for i, r in zip(fold["validation"], validation, strict=True):
            outputs["majority"][i] = majority
            outputs["rule"][i] = 0.85 if any(t in r["text"].lower() for t in CONFIG["rule_tokens"]) else 0.15
        for representation in CONFIG["representations"]:
            x, blocks = prepare_features(training, representation)
            model = train_head(x, blocks, targets, representation, "capability")
            parity(model, x, training)
            for i, r in zip(fold["validation"], validation, strict=True):
                outputs[representation][i] = (
                    predict(model, r["text"])["probabilities"][ERROR] if vector(model, r["text"]) else 1.0
                )
            parameters.append({"fold": number, "representation": representation, "model": model})
    if any(p is None for pp in outputs.values() for p in pp):
        raise ValueError("missing proxy out-of-fold output")
    report = {
        "experiment_id": CONFIG["id"],
        "n": len(rows),
        "semantic_families": len({r["semantic_family"] for r in rows}),
        "engine_error_records": sum(truth),
        "engine_error_families": len({r["semantic_family"] for r in rows if error[r["id"]]}),
        "models": {name: [binary_metrics(truth, pp, t) for t in CONFIG["thresholds"]] for name, pp in outputs.items()},
        "folds": folds,
        "parameter_sha256": digest(parameters),
        "dataset_sha256": digest(manifest),
        "label_sha256": digest(error),
        "ood_truth_count": 0,
        "ood_precision": None,
        "ood_recall": None,
        "ood_auc": None,
        "promotion_eligible": False,
        "protected_evidence_consumed": [],
        "gate": "NO_PROMOTION: proxy labels are source engine errors, not reviewed safe-routing or OOD labels",
    }
    if reproduce:
        if read(out / "results.json") != report:
            raise ValueError("engine-error proxy did not reproduce")
        write(
            out / "reproduction.json",
            {"parameters_identical": True, "aggregate_metrics_identical": True, "protected_scoring_runs": 0},
        )
    else:
        write(out / "parameters.json", parameters)
        write(out / "results.json", report)
    print(
        {
            "n": len(rows),
            "error_records": sum(truth),
            "error_families": report["engine_error_families"],
            "promotion_eligible": False,
        }
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=Path("/workspace/router-runs/blux-router-0.2"))
    p.add_argument("--workspace", type=Path, default=Path("/workspace"))
    p.add_argument("--out", type=Path, default=Path("/workspace/router-runs/blux-engine-error-proxy-0.1"))
    p.add_argument("--reproduce", action="store_true")
    args = p.parse_args()
    run(args.data, args.workspace, args.out, args.reproduce)


if __name__ == "__main__":
    main()
