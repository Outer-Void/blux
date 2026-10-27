"""Route, abstention, OOD and calibration metrics with explicit numerators and denominators."""

from __future__ import annotations

import math
from collections import Counter

from training.phase2.models import family
from training.phase2.protocol import ABSTAIN, REASONS


def ratio(n: int | float, d: int) -> float | None:
    return n / d if d else None


def metrics(rows: list[dict], predictions: list[dict], capabilities: list[str]) -> dict:
    if not rows or len(rows) != len(predictions):
        raise ValueError("empty/mismatched evaluation")
    labels = [*capabilities, ABSTAIN]
    matrix = {c: {p: 0 for p in labels} for c in labels}
    correct = argmax_correct = family_correct = accepted = accepted_correct = route_n = route_correct = 0
    tp = fp = fn = 0
    nll = brier = 0.0
    bins = [{"n": 0, "correct": 0, "confidence_sum": 0.0} for _ in range(10)]
    positives, negatives = [], []
    behavior = {r: {"n": 0, "abstained": 0} for r in REASONS}
    for row, pred in zip(rows, predictions, strict=True):
        truth, decision, guess = row["label"], pred["prediction"], pred["argmax"]
        if truth not in labels or decision not in labels or guess not in labels:
            raise ValueError("unsupported capability in metrics")
        matrix[truth][decision] += 1
        right = truth == decision
        correct += right
        argmax_correct += truth == guess
        family_probs = {
            f: sum(p for c, p in pred["probabilities"].items() if family(c) == f) for f in ("ca", "coga", ABSTAIN)
        }
        family_correct += family(truth) == max(family_probs, key=family_probs.get)
        take = decision != ABSTAIN
        accepted += take
        accepted_correct += take and right
        route_n += truth != ABSTAIN
        route_correct += truth != ABSTAIN and right
        tp += truth == ABSTAIN and not take
        fp += truth != ABSTAIN and not take
        fn += truth == ABSTAIN and take
        probs = pred["probabilities"]
        nll -= math.log(max(probs.get(truth, 0), 1e-15))
        brier += sum((probs.get(c, 0) - int(c == truth)) ** 2 for c in labels)
        confidence = pred["confidence"]
        bucket = bins[min(9, int(confidence * 10))]
        bucket["n"] += 1
        bucket["correct"] += truth == guess
        bucket["confidence_sum"] += confidence
        score = pred["abstention_score"]
        (positives if truth == ABSTAIN else negatives).append(score)
        if row["reason"] in behavior:
            behavior[row["reason"]]["n"] += 1
            behavior[row["reason"]]["abstained"] += not take
    perclass = {}
    for label in labels:
        a = matrix[label][label]
        actual = sum(matrix[label].values())
        proposed = sum(matrix[t][label] for t in labels)
        p, r = a / proposed if proposed else 0, a / actual if actual else 0
        perclass[label] = {
            "precision": p,
            "recall": r,
            "f1": 2 * p * r / (p + r) if p + r else 0,
            "tp": a,
            "true_count": actual,
            "predicted_count": proposed,
        }
    present = [c for c in labels if perclass[c]["true_count"]]
    n = len(rows)
    auc_pairs = len(positives) * len(negatives)
    auc = ratio(sum(1 if a > b else 0.5 if a == b else 0 for a in positives for b in negatives), auc_pairs)
    for value in behavior.values():
        value["abstention_recall"] = ratio(value["abstained"], value["n"])
    return {
        "n": n,
        "semantic_families": len({r["semantic_family"] for r in rows}),
        "class_counts": dict(sorted(Counter(r["label"] for r in rows).items())),
        "accuracy": correct / n,
        "correct": correct,
        "argmax_accuracy": argmax_correct / n,
        "capability_accuracy": ratio(route_correct, route_n),
        "capability_correct": route_correct,
        "route_truth_count": route_n,
        "family_accuracy": family_correct / n,
        "family_correct": family_correct,
        "macro_f1": sum(v["f1"] for v in perclass.values()) / len(labels),
        "authoritative_class_macro_f1": sum(perclass[c]["f1"] for c in capabilities) / len(capabilities),
        "present_class_macro_f1": sum(perclass[c]["f1"] for c in present) / len(present),
        "per_class": perclass,
        "confusion_matrix": matrix,
        "nll": nll / n,
        "brier": brier / n,
        "ece": sum(abs(b["correct"] - b["confidence_sum"]) for b in bins) / n,
        "calibration_bins": bins,
        "accepted": accepted,
        "accepted_correct": accepted_correct,
        "coverage": accepted / n,
        "selective_accuracy": ratio(accepted_correct, accepted),
        "abstention_rate": 1 - accepted / n,
        "abstention": {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "truth_count": len(positives),
            "precision": ratio(tp, tp + fp),
            "recall": ratio(tp, tp + fn),
            "roc_auc": auc,
            "auc_positive_count": len(positives),
            "auc_negative_count": len(negatives),
        },
        "behavior": behavior,
    }
