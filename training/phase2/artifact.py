"""Standard-library-only validation and fallback for separately versioned 0.2 artifacts."""

from __future__ import annotations

import math
from pathlib import Path

from training.phase2.models import predict
from training.phase2.protocol import ABSTAIN
from training.router.common import digest, read


def validate(envelope: dict, authoritative: list[str], *, allow_development: bool = False) -> dict:
    if set(envelope) != {"payload", "sha256"} or digest(envelope["payload"]) != envelope["sha256"]:
        raise ValueError("artifact checksum mismatch")
    m = envelope["payload"]
    if (m["schema"], m["schema_version"], m["model_id"], m["model_version"]) != (
        "blux.router02-artifact",
        1,
        "blux-router-0.2",
        "0.2.0",
    ):
        raise ValueError("artifact/model mismatch")
    if m["stage"] != "development-only" or not allow_development:
        raise ValueError("development artifact is not eligible for normal routing")
    if m["authoritative_capabilities"] != authoritative or m["promotion_eligible"]:
        raise ValueError("unsupported capability or promotion mismatch")
    if not m["dataset_sha256"] or not m["source_commits"] or not m["code_sha256"]:
        raise ValueError("missing artifact provenance")
    for architecture, model in m["models"].items():
        if architecture != model["architecture"]:
            raise ValueError("architecture identity mismatch")
        label_universe = {"ca", "coga", ABSTAIN} if architecture == "family-only" else {*authoritative, ABSTAIN}
        labels = model["supported_labels"]
        if not labels or len(set(labels)) != len(labels) or not set(labels) <= label_universe:
            raise ValueError("unknown supported capability")
        if set(labels) != {c for c, n in model["training_counts"].items() if n > 0}:
            raise ValueError("unsupported class treated as learned support")
        nfeatures = sum(len(block["vocabulary"]) for block in model["features"])
        if not 0 < nfeatures <= 100000:
            raise ValueError("invalid feature count")
        for block in model["features"]:
            if block["kind"] not in ("word", "char") or len(block["vocabulary"]) != len(block["idf"]):
                raise ValueError("invalid feature configuration")
            if len(set(block["vocabulary"])) != len(block["vocabulary"]) or any(
                not isinstance(t, str) for t in block["vocabulary"]
            ):
                raise ValueError("invalid feature vocabulary")
            if not math.isfinite(block["scale"]) or not 0 < block["scale"] <= 1:
                raise ValueError("invalid feature scale")
            if any(not math.isfinite(v) or v <= 0 for v in block["idf"]):
                raise ValueError("invalid IDF")
        learned = []
        for key, head in model["heads"].items():
            if key != "family":
                learned.extend(head["labels"])
            if len(head["labels"]) != len(head["weights"]) or len(head["labels"]) != len(head["bias"]):
                raise ValueError("invalid head dimensions")
            if any(len(weights) != nfeatures for weights in head["weights"]):
                raise ValueError("invalid coefficient dimensions")
            if any(not math.isfinite(v) for weights in head["weights"] for v in weights) or any(
                not math.isfinite(v) for v in head["bias"]
            ):
                raise ValueError("invalid coefficients")
        if sorted(learned) != sorted(labels):
            raise ValueError("model label mismatch")
        if (
            not math.isfinite(model["temperature"])
            or not 0 < model["temperature"] <= 10
            or not 0 <= model["threshold"] <= 1
        ):
            raise ValueError("invalid calibration")
        calibration = model["calibration"]
        if calibration["temperature"] != model["temperature"] or calibration["threshold"] != model["threshold"]:
            raise ValueError("calibration metadata corruption")
    return m


def load(path: Path, capabilities: list[str], *, allow_development: bool = False) -> dict:
    if path.stat().st_size > 32_000_000:
        raise ValueError("artifact too large")
    return validate(read(path), capabilities, allow_development=allow_development)


def advisory_or_fallback(
    path: Path,
    text: str,
    capabilities: list[str],
    fallback,
    *,
    allow_development: bool = False,
    architecture: str = "word-char-threshold",
):
    try:
        model = load(path, capabilities, allow_development=allow_development)["models"][architecture]
        prediction = predict(model, text)
    except (OSError, ValueError, KeyError, TypeError, OverflowError, IndexError):
        return fallback()
    return fallback() if prediction["prediction"] == ABSTAIN else prediction
