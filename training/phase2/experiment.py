"""Development-only source comparisons. Complete/fresh corpus evidence is required for promotion."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import platform
from datetime import datetime, timezone
from pathlib import Path

from training.phase2.artifact import load
from training.phase2.baselines import family_predictions, predictions
from training.phase2.dataset import build, fit_rows, load_verified
from training.phase2.metrics import metrics
from training.phase2.models import calibrate, family, predict, train
from training.phase2.protocol import ABSTAIN, CONFIG, GATE
from training.phase2.source import historical_snapshot, inventory
from training.router.common import digest, read, write


def code_digest() -> str:
    root = Path(__file__).parent
    return digest({p.name: p.read_text() for p in sorted(root.glob("*.py"))})


def receipt(out: Path, phase: str, details: dict) -> None:
    path = out / "receipts.json"
    chain = read(path) if path.exists() else []
    chain.append({"phase": phase, "previous_sha256": digest(chain[-1]) if chain else None, "details": details})
    write(path, chain)


def verify_receipts(out: Path) -> dict:
    chain = read(out / "receipts.json")
    previous = None
    for r in chain:
        if r["previous_sha256"] != previous:
            raise ValueError("broken Phase-2 receipt chain")
        if "file" in r["details"]:
            target = Path(r["details"]["file"])
            if target.name != str(target) or digest(read(out / target)) != r["details"]["sha256"]:
                raise ValueError("stale Phase-2 evidence digest")
        previous = digest(r)
    if any(r["phase"] in ("test", "adversarial", "regression", "holdout") for r in chain):
        raise ValueError("development-only run must not contain protected scoring")
    return {"entries": len(chain), "verified": True, "protected_scoring_runs": 0}


def prerequisite_gate(coverage: dict, protected_metrics: dict | None = None) -> dict:
    reasons = list(coverage["problems"])
    if not coverage["ready"]:
        reasons.append("complete, diverse, abstention-aware training and fresh protected corpus absent")
    if protected_metrics is None:
        reasons.append("no protected evaluation: data prerequisites must pass before a candidate is frozen")
    return {
        "status": "BLOCKED_DATA" if not coverage["ready"] else "NOT_EVALUATED",
        "passed": False,
        "normal_runtime_integration": False,
        "reasons": reasons,
        "predeclared_gate": GATE,
    }


def compare(rows: list[dict], capabilities: list[str], canaries: list[str]) -> tuple[dict, dict]:
    development = {
        s: fit_rows([r for r in rows if r["split"] == s], s, capabilities, canaries) for s in ("train", "validation")
    }
    training, validation = development["train"], development["validation"]
    if {r["semantic_family"] for r in training} & {r["semantic_family"] for r in validation}:
        raise ValueError("semantic family leakage at training boundary")
    comparison = {
        "evaluation_scope": "development-only; no fresh protected corpus",
        "models": {},
        "baselines": {},
        "train_n": len(training),
        "validation_n": len(validation),
    }
    baseline_outputs = {}
    for name in ("majority", "keyword", "deterministic-fallback"):
        output = predictions(validation, training, capabilities, name)
        baseline_outputs[name] = output
        comparison["baselines"][name] = metrics(validation, output, capabilities)
    models = {}
    for name in CONFIG["candidates"]:
        if name == "word-char-explicit-abstain" and not any(r["label"] == ABSTAIN for r in training):
            comparison["models"][name] = {
                "status": "NOT_TRAINED",
                "reason": "no rights-cleared abstention/OOD training examples",
            }
            continue
        model = train(training, capabilities, canaries, name)
        model, calibration = calibrate(model, validation, capabilities, canaries)
        result = metrics(validation, [predict(model, r["text"]) for r in validation], capabilities)
        comparison["models"][name] = {
            "status": "DEVELOPMENT_ONLY",
            "metrics": result,
            "calibration": calibration,
            "supported_labels": model["supported_labels"],
            "unsupported_capabilities": sorted(set(capabilities) - set(model["supported_labels"])),
        }
        models[name] = model
    family_model = train(training, capabilities, canaries, "family-only")
    family_model["calibration"] = {
        "temperature": 1.0,
        "threshold": 0.0,
        "target_met": False,
        "scope": "uncalibrated family diagnostic; no OOD evidence",
    }
    family_rows = [{**r, "label": family(r["label"])} for r in validation]
    family_learned = metrics(family_rows, [predict(family_model, r["text"]) for r in validation], ["ca", "coga"])
    family_rule = metrics(family_rows, family_predictions(baseline_outputs["keyword"]), ["ca", "coga"])
    comparison["family_diagnostic"] = {
        "scope": "family-only development diagnostic",
        "learned": family_learned,
        "deterministic_keyword_family": family_rule,
        "promotion_claim": False,
    }
    models["family-only"] = family_model
    # Hybrid is a separate competitor. The unchanged keyword baseline is never replaced or weakened.
    learned = [predict(models["word-char-threshold"], r["text"]) for r in validation]
    hybrid = [
        rule if rule["prediction"] != ABSTAIN else model
        for rule, model in zip(baseline_outputs["deterministic-fallback"], learned, strict=True)
    ]
    comparison["hybrid"] = metrics(validation, hybrid, capabilities)
    comparison["selection"] = {
        "frozen_promotion_candidate": None,
        "reason": "missing capability, diversity, abstention and fresh protected evidence; no model is promoted",
    }
    return models, comparison


def develop(workspace: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "development.started").open("x") as f:
        f.write("blux-router-0.2 development-only\n")
    rows, source, quarantine, canaries = inventory(workspace, out)
    labels = sorted(c for values in source["lineage"]["capabilities"].values() for c in values)
    manifest = build(rows, out / "dataset", labels, canaries, quarantine, source)
    rows, manifest = load_verified(out / "dataset", digest(manifest), labels, canaries, quarantine)
    write(out / "coverage.json", manifest["report"])
    write(
        out / "protocol.json", {"config": CONFIG, "gate": GATE, "corpus_requirements": manifest["report"]["problems"]}
    )
    receipt(out, "source-audit", {"file": "source-audit.json", "sha256": digest(source)})
    receipt(out, "coverage", {"file": "coverage.json", "sha256": digest(manifest["report"])})
    models, comparison = compare(rows, labels, canaries)
    write(out / "development-metrics.json", comparison)
    payload = {
        "schema": "blux.router02-artifact",
        "schema_version": 1,
        "model_id": "blux-router-0.2",
        "model_version": "0.2.0",
        "stage": "development-only",
        "promotion_eligible": False,
        "authoritative_capabilities": labels,
        "models": models,
        "dataset_sha256": digest(manifest),
        "source_commits": source["starting_commits"],
        "code_sha256": code_digest(),
        "training_timestamp": datetime.now(timezone.utc).isoformat(),
        "seed": CONFIG["seed"],
        "training_config": CONFIG,
        "python": platform.python_version(),
        "dependencies": {
            n: importlib.metadata.version(n)
            for n in ("scikit-learn", "numpy", "scipy", "joblib", "threadpoolctl", "jsonschema", "PyYAML")
        },
        "metrics_sha256": digest(comparison),
        "provenance": "existing eligible source records only; no generated prose",
    }
    write(out / "model.json", {"payload": payload, "sha256": digest(payload)})
    load(out / "model.json", labels, allow_development=True)
    verdict = prerequisite_gate(manifest["report"])
    write(out / "gate.json", verdict)
    receipt(out, "development-comparison", {"file": "development-metrics.json", "sha256": digest(comparison)})
    receipt(out, "development-artifact", {"file": "model.json", "sha256": digest(read(out / "model.json"))})
    receipt(out, "blocked-data", {"file": "gate.json", "sha256": digest(verdict)})
    if historical_snapshot(workspace / "blux") != source["phase1_files"]:
        raise ValueError("Phase-1 historical files changed")
    print(
        {
            "status": verdict["status"],
            "train": comparison["train_n"],
            "validation": comparison["validation_n"],
            "protected_scoring_runs": 0,
            "model_file_sha256": hashlib.sha256((out / "model.json").read_bytes()).hexdigest(),
        }
    )


def reproduce(out: Path) -> None:
    artifact = read(out / "model.json")["payload"]
    if code_digest() != artifact["code_sha256"]:
        raise ValueError("development model code changed")
    if any(importlib.metadata.version(n) != v for n, v in artifact["dependencies"].items()):
        raise ValueError("reproduction dependency mismatch")
    labels = artifact["authoritative_capabilities"]
    rows, _ = load_verified(
        out / "dataset", artifact["dataset_sha256"], labels, read(out / "canaries.json"), read(out / "quarantine.json")
    )
    models, comparison = compare(rows, labels, read(out / "canaries.json"))
    if digest(models) != digest(artifact["models"]) or digest(comparison) != artifact["metrics_sha256"]:
        raise ValueError("development reproduction failed")
    result = {
        "parameters_identical": True,
        "development_metrics_identical": True,
        "allowed_records": len(rows),
        "protected_scoring_runs": 0,
        "code_sha256": code_digest(),
    }
    write(out / "reproduction.json", result)
    receipt(out, "allowed-data-reproduction", {"file": "reproduction.json", "sha256": digest(result)})
    print(result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["develop", "reproduce", "verify", "evaluate"])
    parser.add_argument("--workspace", type=Path, default=Path("/workspace"))
    parser.add_argument("--out", type=Path, default=Path("/workspace/router-runs/blux-router-0.2"))
    args = parser.parse_args()
    if args.out.resolve().is_relative_to((args.workspace / "blux").resolve()):
        raise ValueError("raw corpus and model parameters belong outside the checkout")
    if args.action == "develop":
        develop(args.workspace, args.out)
    elif args.action == "reproduce":
        reproduce(args.out)
    elif args.action == "verify":
        print(verify_receipts(args.out))
    else:
        # This command must not open any protected rows while the required corpus is absent.
        manifest = read(args.out / "dataset/manifest.json")
        if not manifest["report"]["ready"]:
            raise ValueError("protected evaluation blocked: fresh complete abstention-aware corpus required")
        raise ValueError("no promotion candidate is frozen; this development-only run cannot evaluate protected splits")


if __name__ == "__main__":
    main()
