"""Predeclared baselines: training-only majority and source-contract keyword cues."""

from __future__ import annotations

import re
from collections import Counter

from training.router.metrics import family

# Cues describe deterministic contracts. They never read a protected example or expected output.
CUES = {
    "reason.analyze": ("analy", "evidence", "claim", "hypothes", "support", "contradict"),
    "reason.compare": ("compar", "tradeoff", "criteria", "pareto", "weighted"),
    "reason.critique": ("criti", "circular", "unsupported", "argument"),
    "reason.decompose": ("decompos", "work tree", "acceptance", "parts"),
    "reason.plan": ("plan", "schedul", "dependenc", "parallel", "critical path"),
    "reason.rank": ("rank", "pairwise", "preference", "copeland"),
    "reason.risk": ("risk", "likelihood", "impact", "mitigat"),
    "artifact.construct": ("scaffold", "construct", "blueprint", "template"),
    "artifact.modify": ("replace", "rename", "update", "edit", "change", "upgrade", "migrat"),
    "artifact.repair": ("repair", "dat", "finding", "bounded"),
    "artifact.validate": ("changeset integrity", "validate changeset"),
    "code.patch": ("single-file", "single file", "unified diff"),
    "repo.patch": ("multi-file", "multi file", "all or nothing"),
    "config.generate": ("generat", "canonical", "config", "toml", "yaml", "ini"),
}


def baseline_predictions(rows: list[dict], train: list[dict], labels: list[str], kind: str) -> list[dict]:
    counts = Counter(r["label"] for r in train)
    majority = max(labels, key=lambda c: (counts[c], c))
    if set(CUES) != set(labels):
        raise ValueError("baseline cues do not match authoritative capabilities")
    out = []
    for row in rows:
        if kind == "majority":
            probs = {c: counts[c] / len(train) for c in labels}
            guess = majority
        elif kind == "keyword":
            text = re.sub(r"\s+", " ", row["text"].lower())
            scores = {c: sum(cue in text for cue in CUES[c]) for c in labels}
            guess = max(labels, key=lambda c: (scores[c], c == majority, c))
            probs = {c: 0.85 if c == guess else 0.15 / (len(labels) - 1) for c in labels}
        else:
            raise ValueError("unknown baseline")
        family_probs = {f: sum(p for c, p in probs.items() if family(c) == f) for f in ("ca", "coga")}
        out.append(
            {
                "proposed_capability": guess,
                "confidence": probs[guess],
                "probabilities": probs,
                "family_probabilities": family_probs,
                "decision": "ROUTE",
            }
        )
    return out
