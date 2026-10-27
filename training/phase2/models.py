"""Small inspectable candidates; source-only diagnostics never bypass corpus promotion prerequisites."""

from __future__ import annotations

import copy
import math
import re
from collections import Counter

from training.phase2.protocol import ABSTAIN, CONFIG


def family(label: str) -> str:
    return label if label in (ABSTAIN, "ca", "coga") else ("coga" if label.startswith("reason.") else "ca")


def tokens(kind: str, text: str) -> list[str]:
    text = text.lower()
    if kind == "word":
        words = re.findall(r"(?u)\b\w\w+\b", text)
        return words + [" ".join(words[i : i + 2]) for i in range(len(words) - 1)]
    if kind == "char":
        text = re.sub(r"\s\s+", " ", text)
        return [text[i : i + n] for n in range(3, 6) for i in range(len(text) - n + 1)]
    raise ValueError("unsupported feature block")


def vector(model: dict, text: str) -> dict[int, float]:
    result, offset = {}, 0
    for block in model["features"]:
        counts = Counter(tokens(block["kind"], text))
        values = {i: counts[t] * block["idf"][i] for i, t in enumerate(block["vocabulary"]) if t in counts}
        norm = math.sqrt(sum(v * v for v in values.values()))
        if norm:
            result.update({offset + i: v / norm * block["scale"] for i, v in values.items()})
        offset += len(block["vocabulary"])
    return result


def softmax(classifier: dict, x: dict[int, float], temperature: float) -> dict:
    logits = [
        (bias + sum(w[i] * v for i, v in x.items())) / temperature
        for w, bias in zip(classifier["weights"], classifier["bias"], strict=True)
    ]
    biggest = max(logits)
    values = [math.exp(v - biggest) for v in logits]
    return {label: value / sum(values) for label, value in zip(classifier["labels"], values, strict=True)}


def predict(model: dict, text: str) -> dict:
    x = vector(model, text)
    if model["kind"] == "hierarchy":
        families = softmax(model["heads"]["family"], x, model["temperature"])
        probabilities = {
            c: families[f] * p
            for f in ("ca", "coga")
            for c, p in softmax(model["heads"][f], x, model["temperature"]).items()
        }
    else:
        probabilities = softmax(model["heads"]["direct"], x, model["temperature"])
    guess = max(probabilities, key=lambda c: (probabilities[c], c))
    confidence = probabilities[guess]
    decision = ABSTAIN if not x or guess == ABSTAIN or confidence < model["threshold"] else guess
    return {
        "prediction": decision,
        "argmax": guess,
        "confidence": confidence,
        "probabilities": probabilities,
        "abstention_score": probabilities.get(ABSTAIN, 1 - max(probabilities.values())),
        "authority": "advisory-only",
        "certified": False,
    }


def _feature_matrix(rows: list[dict], word_char: bool):
    import scipy.sparse as sp
    from sklearn.feature_extraction.text import TfidfVectorizer

    kinds = ["word", "char"] if word_char else ["word"]
    blocks, matrices = [], []
    for kind in kinds:
        v = TfidfVectorizer(
            analyzer="word" if kind == "word" else "char",
            ngram_range=(1, 2) if kind == "word" else (3, 5),
            norm="l2",
            smooth_idf=True,
        )
        scale = 1 / math.sqrt(len(kinds))
        matrices.append(v.fit_transform([r["text"] for r in rows]) * scale)
        blocks.append(
            {"kind": kind, "vocabulary": v.get_feature_names_out().tolist(), "idf": v.idf_.tolist(), "scale": scale}
        )
    return sp.hstack(matrices, format="csr"), blocks


def train(
    rows: list[dict], labels: list[str], canaries: list[str], architecture: str, *, allow_test: bool = False
) -> dict:
    from threadpoolctl import threadpool_limits

    from training.phase2.dataset import fit_rows
    from training.router.fit import head

    fit_rows(rows, "train", labels, canaries, allow_test=allow_test)
    if architecture not in (*CONFIG["candidates"], "family-only"):
        raise ValueError("unknown architecture")
    explicit = architecture == "word-char-explicit-abstain"
    if explicit and not any(r["label"] == ABSTAIN for r in rows):
        raise ValueError("explicit learned abstention requires abstention training examples")
    selected = rows if explicit or architecture == "family-only" else [r for r in rows if r["label"] != ABSTAIN]
    if not selected:
        raise ValueError("no eligible model training records")
    x, blocks = _feature_matrix(selected, architecture != "word-hierarchy-reference")
    with threadpool_limits(limits=1):
        if architecture == "word-hierarchy-reference":
            heads = {"family": head(x, [family(r["label"]) for r in selected])}
            for f in ("ca", "coga"):
                ii = [i for i, r in enumerate(selected) if family(r["label"]) == f]
                heads[f] = head(x[ii], [selected[i]["label"] for i in ii])
        else:
            targets = [family(r["label"]) if architecture == "family-only" else r["label"] for r in selected]
            heads = {"direct": head(x, targets)}
    model = {
        "architecture": architecture,
        "kind": "hierarchy" if architecture == "word-hierarchy-reference" else "direct",
        "features": blocks,
        "heads": heads,
        "temperature": 1.0,
        "threshold": 0.0,
        "supported_labels": sorted(
            {family(r["label"]) if architecture == "family-only" else r["label"] for r in selected}
        ),
        "training_counts": dict(
            Counter(family(r["label"]) if architecture == "family-only" else r["label"] for r in selected)
        ),
    }
    for i, r in enumerate(selected):
        expected = x.getrow(i).toarray()[0]
        actual = vector(model, r["text"])
        if any(abs(v - actual.get(j, 0)) > 1e-12 for j, v in enumerate(expected)):
            raise ValueError("portable feature export disagrees with sklearn")
    return model


def calibrate(
    model: dict, validation: list[dict], labels: list[str], canaries: list[str], *, allow_test: bool = False
) -> tuple[dict, dict]:
    from training.phase2.dataset import fit_rows
    from training.phase2.metrics import metrics

    fit_rows(validation, "validation", labels, canaries, allow_test=allow_test)
    trials = []
    for temperature in CONFIG["temperatures"]:
        model["temperature"] = temperature
        results = metrics(validation, [predict(model, r["text"]) for r in validation], labels)
        trials.append({"temperature": temperature, "nll": results["nll"]})
    model["temperature"] = min(trials, key=lambda t: (t["nll"], t["temperature"]))["temperature"]
    thresholds = []
    for threshold in CONFIG["thresholds"]:
        model["threshold"] = threshold
        m = metrics(validation, [predict(model, r["text"]) for r in validation], labels)
        thresholds.append(
            {
                "threshold": threshold,
                "coverage": m["coverage"],
                "selective_accuracy": m["selective_accuracy"],
                "abstain_precision": m["abstention"]["precision"],
                "abstain_recall": m["abstention"]["recall"],
            }
        )
    valid = [
        t
        for t in thresholds
        if t["coverage"] >= CONFIG["validation_min_coverage"]
        and t["selective_accuracy"] is not None
        and t["selective_accuracy"] >= CONFIG["validation_min_selective_accuracy"]
        and t["abstain_recall"] is not None
        and t["abstain_recall"] >= CONFIG["validation_min_abstain_recall"]
        and t["abstain_precision"] is not None
        and t["abstain_precision"] >= CONFIG["validation_min_abstain_precision"]
    ]
    model["threshold"] = min(valid, key=lambda t: (-t["coverage"], t["threshold"]))["threshold"] if valid else 1.0
    calibration = {
        "temperature_trials": trials,
        "threshold_trials": thresholds,
        "target_met": bool(valid),
        "temperature": model["temperature"],
        "threshold": model["threshold"],
    }
    model["calibration"] = calibration
    return copy.deepcopy(model), calibration
