"""Only train/validation can enter vocabulary, classifier or calibration fitting."""

from __future__ import annotations

import copy
from collections import Counter

from training.router.data import assert_fitting
from training.router.inference import predict
from training.router.metrics import evaluate, family

CONFIG = {
    "seed": 1729,
    "C": 4.0,
    "class_weight": "balanced",
    "max_iter": 2000,
    "solver": "lbfgs",
    "temperatures": [0.5, 0.75, 1.0, 1.5, 2.0],
    "thresholds": [0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0],
    "min_validation_accepted": 8,
    "min_validation_selective_accuracy": 0.9,
}
FEATURE_CONFIG = {"analyzer": "word", "ngram_range": [1, 2], "norm": "l2", "smooth_idf": True}


def head(x, y: list[str]) -> dict:
    import numpy as np
    from sklearn.linear_model import LogisticRegression

    if len(set(y)) == 1:
        return {"labels": sorted(set(y)), "weights": [[0.0] * x.shape[1]], "bias": [0.0]}
    model = LogisticRegression(
        C=CONFIG["C"],
        class_weight=CONFIG["class_weight"],
        max_iter=CONFIG["max_iter"],
        solver=CONFIG["solver"],
        random_state=CONFIG["seed"],
    )
    model.fit(x, y)
    weights, bias = model.coef_, model.intercept_
    if len(model.classes_) == 2:
        weights = np.vstack([-weights[0] / 2, weights[0] / 2])
        bias = np.array([-bias[0] / 2, bias[0] / 2])
    return {"labels": model.classes_.tolist(), "weights": weights.tolist(), "bias": bias.tolist()}


def fit(train: list[dict], validation: list[dict], canaries: list[str], labels: list[str]) -> tuple[dict, dict]:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from threadpoolctl import threadpool_limits

    assert_fitting(train, canaries)
    assert_fitting(validation, canaries)
    if any(r["split"] != "train" for r in train) or any(r["split"] != "validation" for r in validation):
        raise ValueError("fit/calibration role mismatch")
    if {r["family"] for r in train} & {r["family"] for r in validation}:
        raise ValueError("family isolation failed")
    vectorizer = TfidfVectorizer(ngram_range=(1, 2), norm="l2", smooth_idf=True)
    x = vectorizer.fit_transform([r["text"] for r in train])
    with threadpool_limits(limits=1):
        families = head(x, [family(r["label"]) for r in train])
        heads = {}
        for name in ("ca", "coga"):
            indices = [i for i, r in enumerate(train) if family(r["label"]) == name]
            heads[name] = head(x[indices], [train[i]["label"] for i in indices])
    model = {
        "schema": "blux.router-artifact",
        "schema_version": 1,
        "model_id": "blux-router-0.1",
        "model_version": "0.1.0",
        "model_type": "hierarchical-tfidf-logistic",
        "feature_config": FEATURE_CONFIG,
        "labels": sorted({r["label"] for r in train}),
        "unsupported_capabilities": sorted(set(labels) - {r["label"] for r in train}),
        "vocabulary": vectorizer.get_feature_names_out().tolist(),
        "idf": vectorizer.idf_.tolist(),
        "family": families,
        "heads": heads,
        "temperature": 1.0,
        "threshold": 0.0,
        "training_config": CONFIG,
    }
    calibration = []
    for temperature in CONFIG["temperatures"]:
        model["temperature"] = temperature
        metrics = evaluate(validation, [predict(model, r["text"]) for r in validation], labels)
        calibration.append({"temperature": temperature, "nll": metrics["calibration"]["nll"]})
    model["temperature"] = min(calibration, key=lambda c: (c["nll"], c["temperature"]))["temperature"]
    choices = []
    for threshold in CONFIG["thresholds"]:
        model["threshold"] = threshold
        metrics = evaluate(validation, [predict(model, r["text"]) for r in validation], labels)
        choices.append(
            {
                "threshold": threshold,
                "coverage": metrics["coverage"],
                "accepted": metrics["accepted"],
                "accuracy": metrics["accuracy_when_not_abstaining"],
            }
        )
    passing = [
        c
        for c in choices
        if c["accepted"] >= CONFIG["min_validation_accepted"]
        and c["accuracy"] >= CONFIG["min_validation_selective_accuracy"]
    ]
    model["threshold"] = min(passing, key=lambda c: (-c["coverage"], c["threshold"]))["threshold"] if passing else 1.0
    info = {
        "temperature_selection": calibration,
        "threshold_selection": choices,
        "threshold_target_met": bool(passing),
        "train_counts": dict(Counter(r["label"] for r in train)),
        "train_n": len(train),
        "validation_n": len(validation),
    }
    # Match sklearn features independently from native inference on allowed data only.
    from training.router.inference import features

    for i, row in enumerate(train + validation):
        expected = vectorizer.transform([row["text"]]).toarray()[0]
        actual = features(model, row["text"])
        if any(abs(expected[j] - actual.get(j, 0)) > 1e-12 for j in range(len(expected))):
            raise ValueError(f"portable TF-IDF mismatch at allowed row {i}")
    return copy.deepcopy(model), info
