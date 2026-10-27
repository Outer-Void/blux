"""Portable standard-library inference. Advisory only; not wired to normal BLUX routing."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from training.router.common import digest, read

MAX_BYTES = 8_000_000


def terms(text: str) -> list[str]:
    words = re.findall(r"(?u)\b\w\w+\b", text.lower())
    return words + [" ".join(pair) for pair in zip(words, words[1:], strict=False)]


def features(model: dict, text: str) -> dict[int, float]:
    counts = Counter(terms(text))
    vector = {i: counts[token] * model["idf"][i] for i, token in enumerate(model["vocabulary"]) if token in counts}
    norm = math.sqrt(sum(v * v for v in vector.values()))
    return {i: v / norm for i, v in vector.items()} if norm else {}


def probabilities(head: dict, vector: dict[int, float], temperature: float) -> dict[str, float]:
    scores = [
        (b + sum(weights[i] * v for i, v in vector.items())) / temperature
        for weights, b in zip(head["weights"], head["bias"], strict=False)
    ]
    maximum = max(scores)
    values = [math.exp(x - maximum) for x in scores]
    total = sum(values)
    return dict(zip(head["labels"], (v / total for v in values), strict=False))


def predict(model: dict, text: str) -> dict:
    vector = features(model, text)
    family = probabilities(model["family"], vector, model["temperature"])
    joint = {}
    for name, head in model["heads"].items():
        for label, value in probabilities(head, vector, model["temperature"]).items():
            joint[label] = family[name] * value
    label = max(joint, key=lambda label: (joint[label], label))
    confidence = joint[label]
    abstain = not vector or confidence < model["threshold"]
    return {
        "decision": "ABSTAIN" if abstain else "ROUTE",
        "capability": None if abstain else label,
        "family": None if abstain else ("coga" if label.startswith("reason.") else "ca"),
        "confidence": confidence,
        "proposed_capability": label,
        "probabilities": joint,
        "family_probabilities": family,
        "model_id": model["model_id"],
        "model_version": model["model_version"],
        "authority": "advisory-only",
        "certified": False,
    }


def validate_artifact(envelope: dict, allowed: set[str]) -> dict:
    if set(envelope) != {"payload", "sha256"} or digest(envelope["payload"]) != envelope["sha256"]:
        raise ValueError("artifact checksum mismatch")
    m = envelope["payload"]
    if m["schema"] != "blux.router-artifact" or m["schema_version"] != 1:
        raise ValueError("unsupported artifact version")
    if (
        m["model_id"] != "blux-router-0.1"
        or m["model_version"] != "0.1.0"
        or m["model_type"] != "hierarchical-tfidf-logistic"
    ):
        raise ValueError("unsupported model")
    labels = m["labels"]
    if len(set(labels)) != len(labels) or not set(labels) <= allowed:
        raise ValueError("unknown/duplicate capability")
    n = len(m["vocabulary"])
    if not 0 < n <= 20000 or len(set(m["vocabulary"])) != n or len(m["idf"]) != n:
        raise ValueError("invalid vocabulary dimensions")
    if any(not isinstance(t, str) for t in m["vocabulary"]):
        raise ValueError("invalid vocabulary")
    if set(m["heads"]) != {"ca", "coga"} or m["family"]["labels"] != ["ca", "coga"]:
        raise ValueError("invalid hierarchy")
    combined = []
    for name, head in m["heads"].items():
        if not head["labels"] or any(c.startswith("reason.") != (name == "coga") for c in head["labels"]):
            raise ValueError("invalid family labels")
        combined.extend(head["labels"])
    if sorted(combined) != sorted(labels):
        raise ValueError("inconsistent labels")
    for head in [m["family"], *m["heads"].values()]:
        if len(head["labels"]) != len(head["weights"]) or len(head["bias"]) != len(head["labels"]):
            raise ValueError("invalid classifier dimensions")
        if any(len(w) != n for w in head["weights"]):
            raise ValueError("invalid coefficient dimensions")
        if any(not math.isfinite(v) for w in head["weights"] for v in w) or any(
            not math.isfinite(v) for v in head["bias"]
        ):
            raise ValueError("nonfinite classifier")
    if any(not math.isfinite(v) or v <= 0 for v in m["idf"]):
        raise ValueError("invalid IDF")
    if not math.isfinite(m["temperature"]) or not 0 < m["temperature"] <= 10 or not 0 <= m["threshold"] <= 1:
        raise ValueError("invalid confidence configuration")
    if m["feature_config"] != {"analyzer": "word", "ngram_range": [1, 2], "norm": "l2", "smooth_idf": True}:
        raise ValueError("unsupported features")
    return m


def load_model(path: Path, allowed: set[str]) -> dict:
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("oversized artifact")
    return validate_artifact(read(path), allowed)


def advisory_or_fallback(path: Path, text: str, allowed: set[str], fallback: Callable[[], object]) -> object:
    """Offline adapter seam: errors/abstention invoke the caller's existing provider path unchanged."""
    try:
        decision = predict(load_model(path, allowed), text)
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return fallback()
    return fallback() if decision["decision"] == "ABSTAIN" else decision
