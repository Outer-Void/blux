"""Post-hoc DEVELOPMENT recombination of stored OOF family/capability predictions; no new fit."""

from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from pathlib import Path

from training.phase2.dataset import load_verified
from training.phase2.metrics import metrics
from training.phase2.models import family
from training.router.baselines import CUES
from training.router.common import digest, read, write

CONFIG = {
    "id": "blux-hybrid-router-0.1",
    "hypothesis": "family errors in direct capability routing may be reduced by a separate family decision",
    "candidates": [
        "learned-family-keyword-capability",
        "deterministic-family-learned-capability",
        "learned-family-learned-capability",
    ],
    "family_model": "word-char out-of-fold",
    "capability_model": "word out-of-fold",
    "promotion_eligible": False,
    "scope": "post-hoc development diagnostic; no new training or protected evidence",
}


def restrict_capabilities(probabilities: dict, selected_family: str) -> dict:
    selected = {c: p for c, p in probabilities.items() if family(c) == selected_family}
    total = sum(selected.values())
    if not total:
        return {}
    return {c: p / total for c, p in selected.items()}


def keyword_within_family(
    row: dict, source_training: list[dict], labels: list[str], chosen_family: str, family_probabilities: dict
) -> dict:
    text = " ".join(row["text"].lower().split())
    counts = Counter(r["label"] for r in source_training)
    probabilities = {}
    guesses = {}
    for f in ("ca", "coga"):
        caps = [c for c in labels if family(c) == f]
        majority = max(caps, key=lambda c: (counts[c], c))
        scores = {c: sum(cue in text for cue in CUES[c]) for c in caps}
        guess = max(caps, key=lambda c: (scores[c], c == majority, c))
        guesses[f] = guess
        for c in caps:
            probabilities[c] = family_probabilities.get(f, 0) * (0.85 if c == guess else 0.15 / (len(caps) - 1))
    guess = guesses[chosen_family]
    return {
        "prediction": guess,
        "argmax": guess,
        "probabilities": probabilities,
        "confidence": probabilities[guess],
        "abstention_score": 0.0,
    }


def run(data: Path, research: Path, out: Path, reproduce: bool = False) -> None:
    artifact = read(data / "model.json")["payload"]
    labels = artifact["authoritative_capabilities"]
    rows, manifest = load_verified(
        data / "dataset",
        artifact["dataset_sha256"],
        labels,
        read(data / "canaries.json"),
        read(data / "quarantine.json"),
    )
    if any(r["split"] not in ("train", "validation") for r in rows):
        raise ValueError("protected rows in hybrid diagnostic")
    cv = read(research / "results.json")
    preregistered = read(research / "preregistered.json")
    oof = read(research / "oof-diagnostics.json")
    if cv["dataset_sha256"] != digest(manifest) or cv["protocol_sha256"] != preregistered["protocol_sha256"]:
        raise ValueError("hybrid input dataset/protocol mismatch")
    spec = {
        "config": CONFIG,
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "dataset_sha256": digest(manifest),
        "oof_sha256": digest(oof),
        "source_commits": artifact["source_commits"],
    }
    if not reproduce:
        out.mkdir(parents=True, exist_ok=True)
        with (out / "experiment.started").open("x") as f:
            f.write(CONFIG["id"])
        write(out / "preregistered.json", spec)
    elif read(out / "preregistered.json") != spec:
        raise ValueError("hybrid reproduction inputs changed")
    outputs = {name: [None] * len(rows) for name in CONFIG["candidates"]}
    for fold in preregistered["folds"]:
        training = [rows[i] for i in fold["train"]]
        for i in fold["validation"]:
            learned_family = oof["family"]["word-char"][i]
            deterministic_family = family(oof["capability"]["keyword"][i]["prediction"])
            cap = oof["capability"]["word"][i]
            f = learned_family["prediction"]
            if f in ("ca", "coga"):
                outputs["learned-family-keyword-capability"][i] = keyword_within_family(
                    rows[i], training, labels, f, learned_family["probabilities"]
                )
            else:
                outputs["learned-family-keyword-capability"][i] = dict(cap, prediction="ABSTAIN")
            for name, chosen in [
                ("deterministic-family-learned-capability", deterministic_family),
                ("learned-family-learned-capability", f),
            ]:
                probabilities = restrict_capabilities(cap["probabilities"], chosen)
                if probabilities:
                    guess = max(probabilities, key=lambda c: (probabilities[c], c))
                    result = {
                        "prediction": guess,
                        "argmax": guess,
                        "probabilities": probabilities,
                        "confidence": probabilities[guess],
                        "abstention_score": 1 - probabilities[guess],
                    }
                else:
                    result = dict(cap, prediction="ABSTAIN")
                outputs[name][i] = result
    if any(p is None for v in outputs.values() for p in v):
        raise ValueError("missing hybrid OOF decision")
    report = {
        "id": CONFIG["id"],
        "scope": CONFIG["scope"],
        "n": len(rows),
        "families": len({r["semantic_family"] for r in rows}),
        "models": {name: metrics(rows, pp, labels) for name, pp in outputs.items()},
        "baseline_keyword": cv["capability"]["keyword"],
        "dataset_sha256": digest(manifest),
        "protected_evidence_consumed": [],
        "new_models_fit": 0,
        "promotion_eligible": False,
        "limitation": "conditional probabilities and rule confidence are not OOD calibration or protected evidence",
    }
    if reproduce:
        if read(out / "results.json") != report:
            raise ValueError("hybrid metrics did not reproduce")
        write(
            out / "reproduction.json",
            {"predictions_metrics_identical": True, "new_models_fit": 0, "protected_scoring_runs": 0},
        )
    else:
        write(out / "results.json", report)
    print({name: (m["accuracy"], m["authoritative_class_macro_f1"]) for name, m in report["models"].items()})


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=Path("/workspace/router-runs/blux-router-0.2"))
    p.add_argument("--research", type=Path, default=Path("/workspace/router-runs/development-research-0.1"))
    p.add_argument("--out", type=Path, default=Path("/workspace/router-runs/blux-hybrid-router-0.1"))
    p.add_argument("--reproduce", action="store_true")
    a = p.parse_args()
    run(a.data, a.research, a.out, a.reproduce)


if __name__ == "__main__":
    main()
