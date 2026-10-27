"""Direct deterministic family competitors on the same grouped DEVELOPMENT folds."""

from __future__ import annotations

import argparse
import hashlib
import math
import re
from pathlib import Path

import yaml

from training.phase2.dataset import load_verified
from training.phase2.metrics import metrics
from training.phase2.models import family
from training.research.crossval import paired_family_bootstrap
from training.router.baselines import CUES
from training.router.common import digest, read, write

CONFIG = {
    "experiment_id": "blux-family-baselines-0.1",
    "candidates": ["union-cues", "union-cues-margin2", "contract-overlap"],
    "promotion_eligible": False,
    "protected_evidence_consumed": [],
    "hypothesis": (
        "a direct contract-derived family baseline may explain apparent learned gains over a capability-rule projection"
    ),
    "status": "post-hoc development diagnostic, not a protected comparison",
}
STOP = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "its",
    "no",
    "not",
    "of",
    "or",
    "the",
    "to",
    "with",
}


def rule_predictions(rows: list[dict], method: str, summaries: dict[str, str]) -> list[dict]:
    if method not in CONFIG["candidates"]:
        raise ValueError("unknown family rule")
    cues = {f: {cue for cap, values in CUES.items() if family(cap) == f for cue in values} for f in ("ca", "coga")}
    docs = {cap: set(re.findall(r"[a-z][a-z]+", text.lower())) - STOP for cap, text in summaries.items()}
    vocabulary = set().union(*docs.values())
    idf = {t: math.log((1 + len(docs)) / (1 + sum(t in d for d in docs.values()))) + 1 for t in vocabulary}
    result = []
    for row in rows:
        text = " ".join(row["text"].lower().split())
        if method.startswith("union-cues"):
            scores = {f: sum(cue in text for cue in cc) for f, cc in cues.items()}
        else:
            terms = set(re.findall(r"[a-z][a-z]+", text)) - STOP
            scores = {
                f: max(
                    sum(idf[t] for t in terms & tokens) / math.sqrt(len(tokens))
                    for c, tokens in docs.items()
                    if family(c) == f
                )
                for f in ("ca", "coga")
            }
        ordered = sorted(scores, key=lambda f: (scores[f], f))
        guess = ordered[-1]
        margin = scores[ordered[-1]] - scores[ordered[-2]]
        decision = guess if margin > 0 and (method != "union-cues-margin2" or margin >= 2) else "ABSTAIN"
        logits = {f: math.exp(s - max(scores.values())) for f, s in scores.items()}
        probs = {f: v / sum(logits.values()) for f, v in logits.items()}
        probs["ABSTAIN"] = 0.0
        result.append(
            {
                "prediction": decision,
                "argmax": guess,
                "probabilities": probs,
                "confidence": probs[guess],
                "abstention_score": 1 - probs[guess],
            }
        )
    return result


def assert_development_records(rows: list[dict]) -> None:
    if any(r["split"] not in ("train", "validation") for r in rows):
        raise ValueError("protected data cannot enter a development-only diagnostic")


def run(data: Path, research: Path, workspace: Path, out: Path, reproduce: bool = False) -> None:
    model = read(data / "model.json")["payload"]
    labels = model["authoritative_capabilities"]
    rows, manifest = load_verified(
        data / "dataset", model["dataset_sha256"], labels, read(data / "canaries.json"), read(data / "quarantine.json")
    )
    assert_development_records(rows)
    summaries, contracts = {}, {}
    for engine in ("ca", "coga"):
        path = workspace / f"blux-{engine}/.blux/capabilities.yaml"
        contracts[str(path.relative_to(workspace))] = hashlib.sha256(path.read_bytes()).hexdigest()
        summaries.update({c["capability_id"]: c["summary"] for c in yaml.safe_load(path.read_text())["capabilities"]})
    if sorted(summaries) != labels:
        raise ValueError("contract identity changed")
    code_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if not reproduce:
        out.mkdir(parents=True, exist_ok=True)
        with (out / "experiment.started").open("x") as f:
            f.write(CONFIG["experiment_id"])
        write(
            out / "preregistered.json",
            {"config": CONFIG, "contracts": contracts, "code_sha256": code_sha, "dataset_sha256": digest(manifest)},
        )
    else:
        freeze = read(out / "preregistered.json")
        if freeze != {
            "config": CONFIG,
            "contracts": contracts,
            "code_sha256": code_sha,
            "dataset_sha256": digest(manifest),
        }:
            raise ValueError("family baseline reproduction inputs changed")
    family_rows = [{**r, "label": family(r["label"])} for r in rows]
    oof = read(research / "oof-diagnostics.json")["family"]
    outputs = {method: rule_predictions(rows, method, summaries) for method in CONFIG["candidates"]}
    config = read(research / "preregistered.json")["protocol"]
    report = {
        "experiment_id": CONFIG["experiment_id"],
        "scope": CONFIG["status"],
        "n": len(rows),
        "families": len({r["semantic_family"] for r in rows}),
        "dataset_sha256": digest(manifest),
        "rules": {name: metrics(family_rows, pp, ["ca", "coga"]) for name, pp in outputs.items()},
        "learned_comparison": {
            name: {
                method: paired_family_bootstrap(family_rows, oof[name], pp, config) for method, pp in outputs.items()
            }
            for name in ("word", "word-char")
        },
        "protected_evidence_consumed": [],
        "promotion_eligible": False,
        "contract_digests": contracts,
    }
    if reproduce:
        if report != read(out / "results.json"):
            raise ValueError("family baseline reproduction failed")
        write(out / "reproduction.json", {"aggregate_predictions_metrics_identical": True, "protected_scoring_runs": 0})
    else:
        write(out / "results.json", report)
    print({name: (m["accuracy"], m["coverage"], m["selective_accuracy"]) for name, m in report["rules"].items()})


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=Path("/workspace/router-runs/blux-router-0.2"))
    p.add_argument("--research", type=Path, default=Path("/workspace/router-runs/development-research-0.1"))
    p.add_argument("--workspace", type=Path, default=Path("/workspace"))
    p.add_argument("--out", type=Path, default=Path("/workspace/router-runs/blux-family-baselines-0.1"))
    p.add_argument("--reproduce", action="store_true")
    args = p.parse_args()
    run(args.data, args.research, args.workspace, args.out, args.reproduce)


if __name__ == "__main__":
    main()
