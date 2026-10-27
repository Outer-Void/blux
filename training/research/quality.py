"""Quantitative source/negative-data limitations and development-only lexical/error diagnostics."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

from training.phase2.dataset import load_verified
from training.phase2.models import predict, vector
from training.router.baselines import CUES
from training.router.common import read, write


def auc(positive: list[float], negative: list[float]) -> float | None:
    if not positive or not negative:
        return None
    return sum(1 if a > b else 0.5 if a == b else 0 for a in positive for b in negative) / (
        len(positive) * len(negative)
    )


def distribution(values: list[float]) -> dict:
    values = sorted(values)
    return {
        "n": len(values),
        "min": values[0],
        "median": values[len(values) // 2],
        "max": values[-1],
        "mean": sum(values) / len(values),
        "q25": values[int((len(values) - 1) * 0.25)],
        "q75": values[int((len(values) - 1) * 0.75)],
    }


def random_row_fold_risk(rows: list[dict], n_splits: int, seed: int) -> dict:
    rng = random.Random(seed)
    shuffled = list(range(len(rows)))
    rng.shuffle(shuffled)
    family_folds = defaultdict(set)
    for n, index in enumerate(shuffled):
        family_folds[rows[index]["semantic_family"]].add(n % n_splits)
    leaked = {f for f, splits in family_folds.items() if len(splits) > 1}
    return {
        "hypothetical_only": True,
        "models_trained_on_leaky_splits": 0,
        "families_total": len(family_folds),
        "families_spanning_row_folds": len(leaked),
        "records_affected": sum(r["semantic_family"] in leaked for r in rows),
    }


def analyze(data: Path, research: Path, workspace: Path, out: Path) -> None:
    artifact = read(data / "model.json")["payload"]
    labels = artifact["authoritative_capabilities"]
    rows, manifest = load_verified(
        data / "dataset",
        artifact["dataset_sha256"],
        labels,
        read(data / "canaries.json"),
        read(data / "quarantine.json"),
    )
    byclass = {}
    for label in labels:
        rr = [r for r in rows if r["label"] == label]
        byclass[label] = {
            "records": len(rr),
            "families": len({r["semantic_family"] for r in rr}),
            "train": sum(r["split"] == "train" for r in rr),
            "validation": sum(r["split"] == "validation" for r in rr),
        }
    counts = Counter(r["semantic_family"] for r in rows)
    errors = Counter()
    commands = Counter()
    multi = Counter()
    raw_n = 0
    for name in ("blux-ca-dataset", "blux-coga-dataset"):
        for split in ("train", "validation"):
            recorded = read(workspace / name / "data/manifest.json")
            relative = f"data/{split}.jsonl"
            if (
                hashlib.sha256((workspace / name / relative).read_bytes()).hexdigest()
                != recorded["files"][relative]["sha256"]
            ):
                raise ValueError("source data changed before quality analysis")
            # Only explicitly eligible source splits; never load a protected expected output here.
            for line in (workspace / name / f"data/{split}.jsonl").read_text().splitlines():
                r = json.loads(line)
                raw_n += 1
                inp = r["input"]
                steps = inp.get("steps", [])
                multi[len(steps) if steps else 1] += 1
                commands.update(step["command"] for step in steps if "command" in step)
                failed = r["expected"].get("kind") == "error" or any(
                    s["kind"] == "error" for s in r["expected"].get("steps", [])
                )
                errors[name] += failed
    cue_overlap = Counter()
    for r in rows:
        text = " ".join(r["text"].lower().split())
        matches = [c for c, cues in CUES.items() if any(cue in text for cue in cues)]
        cue_overlap["multiple_capabilities_hit"] += len(matches) > 1
        cue_overlap["label_has_any_cue"] += r["label"] in matches
    training = [r for r in rows if r["split"] == "train"]
    validation = [r for r in rows if r["split"] == "validation"]
    model = artifact["models"]["word-char-threshold"]
    vv = [vector(model, r["text"]) for r in training]
    nearest, confidence, error_flags = [], [], []
    for row in validation:
        x = vector(model, row["text"])
        norm = math.sqrt(sum(v * v for v in x.values()))
        similarities = [
            sum(value * y.get(i, 0) for i, value in x.items()) / (norm * math.sqrt(sum(v * v for v in y.values())))
            if norm and y
            else 0
            for y in vv
        ]
        p = predict(model, row["text"])
        nearest.append(max(similarities))
        confidence.append(p["confidence"])
        error_flags.append(p["argmax"] != row["label"])
    lexical_error_auc = auc(
        [1 - v for v, e in zip(nearest, error_flags, strict=True) if e],
        [1 - v for v, e in zip(nearest, error_flags, strict=True) if not e],
    )
    confidence_error_auc = auc(
        [1 - v for v, e in zip(confidence, error_flags, strict=True) if e],
        [1 - v for v, e in zip(confidence, error_flags, strict=True) if not e],
    )
    risk = []
    for threshold in (0.5, 0.7, 0.9):
        take = [i for i, c in enumerate(confidence) if c >= threshold]
        risk.append(
            {
                "threshold": threshold,
                "accepted": len(take),
                "n": len(validation),
                "incorrect_confident_routes": sum(error_flags[i] for i in take),
                "selective_accuracy": sum(not error_flags[i] for i in take) / len(take) if take else None,
            }
        )
    cv = read(research / "results.json")
    confusions = {}
    for name, m in cv["capability"].items():
        pairs = [
            {"truth": a, "predicted": b, "count": n}
            for a, guesses in m["confusion_matrix"].items()
            for b, n in guesses.items()
            if a != b and n
        ]
        confusions[name] = sorted(pairs, key=lambda p: (-p["count"], p["truth"], p["predicted"]))
    report = {
        "scope": "eligible development data only",
        "dataset_sha256": artifact["dataset_sha256"],
        "raw_eligible_source_records": raw_n,
        "single_label_records": len(rows),
        "independent_declared_families": len(counts),
        "class_counts": byclass,
        "variant_family_size_histogram": dict(sorted(Counter(counts.values()).items())),
        "max_min_nonzero_class_ratio": max(v["records"] for v in byclass.values())
        / min(v["records"] for v in byclass.values() if v["records"]),
        "missing_classes": [k for k, v in byclass.items() if not v["records"]],
        "random_row_fold_risk": random_row_fold_risk(rows, 5, 1729),
        "grouped_cv_family_leaks": 0,
        "source_step_count_histogram": dict(sorted(multi.items())),
        "source_commands": dict(commands),
        "source_records_with_expected_engine_errors": dict(errors),
        "source_error_interpretation": (
            "requested-capability labels do not encode request validity/refusal; no automatic relabeling to ABSTAIN"
        ),
        "contract_cue_overlap": dict(cue_overlap),
        "development_confusions": confusions,
        "lexical_distance": {
            "validation_n": len(validation),
            "nearest_training_cosine": distribution(nearest),
            "error_detection_auc": lexical_error_auc,
            "confidence_error_detection_auc": confidence_error_auc,
            "validation_error_count": sum(error_flags),
            "confidence_threshold_diagnostics": risk,
            "ood_truth_count": 0,
            "ood_precision": None,
            "ood_recall": None,
            "ood_auc": None,
            "interpretation": "error-ranking proxy on development ID data, not OOD detection evidence",
        },
        "promotion_eligible": False,
        "protected_evidence_consumed": [],
    }
    write(out, report)
    print({"records": len(rows), "families": len(counts), "ood_examples": 0, "ood_detection": "NOT_EVALUABLE"})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("/workspace/router-runs/blux-router-0.2"))
    parser.add_argument("--research", type=Path, default=Path("/workspace/router-runs/development-research-0.1"))
    parser.add_argument("--workspace", type=Path, default=Path("/workspace"))
    parser.add_argument(
        "--out", type=Path, default=Path("/workspace/router-runs/development-research-0.1/data-quality.json")
    )
    args = parser.parse_args()
    analyze(args.data, args.research, args.workspace, args.out)


if __name__ == "__main__":
    main()
