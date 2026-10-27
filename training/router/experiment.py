"""Audited, one-shot experiment lifecycle. All large/generated artifacts stay outside Git."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import platform
from datetime import datetime, timezone
from pathlib import Path

from training.router.baselines import baseline_predictions
from training.router.common import digest, read, write
from training.router.data import PROTECTED, assert_lineage, audit, code_digest, load_split
from training.router.fit import CONFIG, fit
from training.router.inference import load_model, predict
from training.router.metrics import evaluate

# Declared before fitting or protected evaluation. Every clause is required.
GATE = {
    "primary_accuracy_margin_over_best_baseline": 0.05,
    "primary_macro_f1_margin_over_best_baseline": 0.05,
    "test_and_holdout_min_coverage": 0.5,
    "min_selective_accuracy": 0.9,
    "test_and_holdout_min_family_accuracy": 0.9,
    "validation_threshold_target_required": True,
    "all_authoritative_capabilities_must_have_training_support": True,
}


def receipt(out: Path, phase: str, details: dict) -> None:
    path = out / "receipts.json"
    chain = read(path) if path.exists() else []
    entry = {"phase": phase, "previous_sha256": digest(chain[-1]) if chain else None, "details": details}
    chain.append(entry)
    write(path, chain)


def prepare(workspace: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    # Reserve before doing any work. Never overwrite a previous experiment/model selection.
    with (out / "prepare.started").open("x") as f:
        f.write("blux-router-0.1\n")
    lineage = audit(workspace, out)
    labels = sorted(c for caps in lineage["capabilities"].values() for c in caps)
    canaries = [d["canary"] for d in lineage["datasets"].values()]
    train, train_counts = load_split(workspace, out, "train", fitting=True)
    validation, val_counts = load_split(workspace, out, "validation", fitting=True)
    baseline_metrics = {
        kind: evaluate(validation, baseline_predictions(validation, train, labels, kind), labels)
        for kind in ("majority", "keyword")
    }
    write(out / "baseline-validation.json", baseline_metrics)  # measured before fitting
    model, selection = fit(train, validation, canaries, labels)
    candidate = evaluate(validation, [predict(model, r["text"]) for r in validation], labels)
    metadata = {
        "lineage": lineage,
        "training_code_sha256": code_digest(),
        "dependencies": {
            n: importlib.metadata.version(n)
            for n in ("scikit-learn", "numpy", "scipy", "joblib", "threadpoolctl", "jsonschema", "PyYAML")
        },
        "python": platform.python_version(),
        "seed": CONFIG["seed"],
        "training_timestamp": datetime.now(timezone.utc).isoformat(),
        "dataset_counts": {"train": train_counts, "validation": val_counts},
        "selection": selection,
        "validation_metrics": candidate,
        "baseline_validation_metrics": baseline_metrics,
        "integration_gate": GATE,
    }
    model["provenance"] = metadata
    write(out / "model.json", {"payload": model, "sha256": digest(model)})
    freeze = {
        "model_file_sha256": hashlib.sha256((out / "model.json").read_bytes()).hexdigest(),
        "parameter_sha256": digest({k: v for k, v in model.items() if k != "provenance"}),
        "metadata": metadata,
    }
    write(out / "freeze.json", freeze)
    receipt(out, "freeze", {"freeze_sha256": digest(freeze), "model_file_sha256": freeze["model_file_sha256"]})
    print("Frozen; no protected scoring has run.")


def check_frozen(workspace: Path, out: Path) -> tuple[dict, dict, list[str]]:
    freeze = read(out / "freeze.json")
    meta = freeze["metadata"]
    if any(importlib.metadata.version(name) != version for name, version in meta["dependencies"].items()):
        raise ValueError("frozen dependency versions changed")
    if meta["training_code_sha256"] != code_digest():
        raise ValueError("training code changed after freeze")
    if hashlib.sha256((out / "model.json").read_bytes()).hexdigest() != freeze["model_file_sha256"]:
        raise ValueError("frozen model file changed")
    assert_lineage(workspace, out, meta["lineage"])
    labels = sorted(c for caps in meta["lineage"]["capabilities"].values() for c in caps)
    model = load_model(out / "model.json", set(labels))
    return freeze, model, labels


def reproduce(workspace: Path, out: Path) -> None:
    freeze, original, labels = check_frozen(workspace, out)
    canaries = [d["canary"] for d in freeze["metadata"]["lineage"]["datasets"].values()]
    train, _ = load_split(workspace, out, "train", fitting=True)
    validation, _ = load_split(workspace, out, "validation", fitting=True)
    model, selection = fit(train, validation, canaries, labels)
    matched = digest(model) == freeze["parameter_sha256"]
    predictions_equal = all(predict(model, r["text"]) == predict(original, r["text"]) for r in train + validation)
    if not matched or not predictions_equal or selection != freeze["metadata"]["selection"]:
        raise ValueError("deterministic reproduction failed")
    result = {
        "parameters_identical": matched,
        "predictions_identical": predictions_equal,
        "allowed_records_checked": len(train) + len(validation),
        "protected_splits_reopened": False,
    }
    write(out / "reproduction.json", result)
    receipt(out, "reproduction", result)
    print(result)


def integration_gate(report: dict, model: dict) -> dict:
    reasons = []
    if model["unsupported_capabilities"]:
        reasons.append("authoritative capabilities lack training support")
    if not model["provenance"]["selection"]["threshold_target_met"]:
        reasons.append("validation selective-accuracy target not met")
    primary = report["test"]
    for metric, margin_key in [
        ("accuracy", "primary_accuracy_margin_over_best_baseline"),
        ("macro_f1", "primary_macro_f1_margin_over_best_baseline"),
    ]:
        if primary["candidate"][metric] < max(primary[k][metric] for k in ("majority", "keyword")) + GATE[margin_key]:
            reasons.append("test " + metric + " does not beat best baseline by required margin")
    for split in PROTECTED:
        m = report[split]["candidate"]
        if (
            m["accuracy_when_not_abstaining"] is None
            or m["accuracy_when_not_abstaining"] < GATE["min_selective_accuracy"]
        ):
            reasons.append(split + " selective accuracy below target or no accepted routes")
        if split in ("test", "holdout"):
            if m["coverage"] < GATE["test_and_holdout_min_coverage"]:
                reasons.append(split + " coverage below target")
            if m["family_routing_accuracy"] < GATE["test_and_holdout_min_family_accuracy"]:
                reasons.append(split + " family accuracy below target")
    return {"passed": not reasons, "reasons": reasons, "normal_runtime_integration": False}


def protected_evaluate(workspace: Path, out: Path) -> None:
    freeze, model, labels = check_frozen(workspace, out)
    if not read(out / "reproduction.json")["parameters_identical"]:
        raise ValueError("reproduction required before protected evaluation")
    with (out / "evaluation.started").open("x") as f:
        f.write(digest(freeze) + "\n")
    train, _ = load_split(workspace, out, "train", fitting=True)
    report = {}
    for split in PROTECTED:  # HOLDOUT is last, after all choices and other evaluations are frozen.
        rows, counts = load_split(workspace, out, split, fitting=False)
        metrics = {"counts": counts, "candidate": evaluate(rows, [predict(model, r["text"]) for r in rows], labels)}
        for kind in ("majority", "keyword"):
            metrics[kind] = evaluate(rows, baseline_predictions(rows, train, labels, kind), labels)
        report[split] = metrics
        write(out / f"{split}-metrics.json", metrics)
        receipt(out, split, {"metrics_sha256": digest(metrics), "n": len(rows)})
    gate = integration_gate(report, model)
    write(out / "gate.json", gate)
    receipt(out, "closed", gate)
    print(gate)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "reproduce", "evaluate"])
    parser.add_argument("--workspace", type=Path, default=Path("/workspace"))
    parser.add_argument("--out", type=Path, default=Path("/workspace/router-runs/blux-router-0.1"))
    args = parser.parse_args()
    if args.out.resolve().is_relative_to((args.workspace / "blux").resolve()):
        raise ValueError("generated model artifacts must remain outside the BLUX checkout")
    {"prepare": prepare, "reproduce": reproduce, "evaluate": protected_evaluate}[args.phase](args.workspace, args.out)


if __name__ == "__main__":
    main()
