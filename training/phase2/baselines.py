"""Phase-1 keyword competitor is reused unchanged; rule abstention and hybrid are separate competitors."""

from __future__ import annotations

from collections import Counter

from training.phase2.models import family
from training.phase2.protocol import ABSTAIN
from training.router.baselines import CUES, baseline_predictions


def predictions(rows: list[dict], train: list[dict], labels: list[str], kind: str) -> list[dict]:
    if kind == "majority":
        counts = Counter(r["label"] for r in train)
        probabilities = {c: counts[c] / len(train) for c in [*labels, ABSTAIN]}
        guess = max(probabilities, key=lambda c: (probabilities[c], c))
        return [
            {
                "prediction": guess,
                "argmax": guess,
                "probabilities": probabilities,
                "confidence": probabilities[guess],
                "abstention_score": probabilities[ABSTAIN],
            }
            for _ in rows
        ]
    projected = [{"text": r["text"], "label": r["label"]} for r in rows]
    training = [{"text": r["text"], "label": r["label"]} for r in train if r["label"] != ABSTAIN]
    keyword = baseline_predictions(projected, training, labels, "keyword")
    result = []
    for row, p in zip(rows, keyword, strict=True):
        guess = p["proposed_capability"]
        if kind == "deterministic-fallback":
            text = " ".join(row["text"].lower().split())
            scores = sorted((sum(cue in text for cue in CUES[c]), c) for c in labels)
            if scores[-1][0] < 2 or scores[-1][0] - scores[-2][0] < 2:
                guess = ABSTAIN
        elif kind != "keyword":
            raise ValueError("unknown baseline")
        probs = (
            {**p["probabilities"], ABSTAIN: 0.0}
            if guess != ABSTAIN
            else {**{c: 0.15 / len(labels) for c in labels}, ABSTAIN: 0.85}
        )
        result.append(
            {
                "prediction": guess,
                "argmax": guess,
                "probabilities": probs,
                "confidence": probs[guess],
                "abstention_score": probs[ABSTAIN],
            }
        )
    return result


def family_predictions(predictions_: list[dict]) -> list[dict]:
    result = []
    for p in predictions_:
        probs = {f: sum(v for c, v in p["probabilities"].items() if family(c) == f) for f in ("ca", "coga", ABSTAIN)}
        guess = max(probs, key=lambda c: (probs[c], c))
        result.append(
            {
                "prediction": family(p["prediction"]),
                "argmax": guess,
                "probabilities": probs,
                "confidence": probs[guess],
                "abstention_score": probs[ABSTAIN],
            }
        )
    return result
