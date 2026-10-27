"""Metrics over every authoritative capability, including unsupported training classes."""

from __future__ import annotations

import math
from collections import Counter


def family(label: str) -> str:
    return "coga" if label.startswith("reason.") else "ca"


def evaluate(rows: list[dict], predictions: list[dict], labels: list[str]) -> dict:
    if not rows or len(rows) != len(predictions):
        raise ValueError("empty/mismatched evaluation")
    matrix = {truth: {guess: 0 for guess in labels} for truth in labels}
    accepted = correct_accepted = correct = family_correct = 0
    nll = brier = 0.0
    bins = [{"n": 0, "confidence_sum": 0.0, "correct": 0} for _ in range(10)]
    for row, pred in zip(rows, predictions, strict=False):
        truth, guess = row["label"], pred["proposed_capability"]
        matrix[truth][guess] += 1
        right = truth == guess
        correct += right
        family_guess = max(pred["family_probabilities"], key=pred["family_probabilities"].get)
        family_correct += family(truth) == family_guess
        take = pred["decision"] == "ROUTE"
        accepted += take
        correct_accepted += take and right
        probs = pred["probabilities"]
        nll -= math.log(max(probs.get(truth, 0), 1e-15))
        brier += sum((probs.get(c, 0) - int(c == truth)) ** 2 for c in labels)
        bucket = bins[min(9, int(pred["confidence"] * 10))]
        bucket["n"] += 1
        bucket["confidence_sum"] += pred["confidence"]
        bucket["correct"] += right
    per_class = {}
    for c in labels:
        tp = matrix[c][c]
        support = sum(matrix[c].values())
        predicted = sum(matrix[t][c] for t in labels)
        precision, recall = tp / predicted if predicted else 0, tp / support if support else 0
        per_class[c] = {
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0,
            "support": support,
        }
    n = len(rows)
    supported = [m["f1"] for m in per_class.values() if m["support"]]
    return {
        "n": n,
        "families": len({r["family"] for r in rows}),
        "accuracy": correct / n,
        "capability_routing_accuracy": correct / n,
        "family_routing_accuracy": family_correct / n,
        "macro_f1": sum(m["f1"] for m in per_class.values()) / len(labels),
        "macro_f1_present_classes": sum(supported) / len(supported),
        "per_class": per_class,
        "confusion_matrix": matrix,
        "accepted": accepted,
        "coverage": accepted / n,
        "abstention_rate": 1 - accepted / n,
        "accuracy_when_not_abstaining": correct_accepted / accepted if accepted else None,
        "calibration": {
            "nll": nll / n,
            "multiclass_brier": brier / n,
            "ece_10_bins": sum(abs(b["correct"] - b["confidence_sum"]) for b in bins) / n,
            "bins": bins,
        },
        "class_counts": dict(sorted(Counter(r["label"] for r in rows).items())),
    }
