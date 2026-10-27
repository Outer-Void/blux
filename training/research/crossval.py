"""Fixed family-grouped DEVELOPMENT research; no fresh protected or OOD generalization claim."""

from __future__ import annotations

import argparse
import math
import random
import warnings
from collections import Counter, defaultdict
from pathlib import Path

from training.phase2.baselines import family_predictions, predictions
from training.phase2.dataset import fit_rows, load_verified
from training.phase2.metrics import metrics
from training.phase2.models import family, predict, vector
from training.phase2.source import historical_snapshot
from training.router.common import digest, read, write


def protocol() -> dict:
    return read(Path(__file__).with_name("protocol.json"))


def source_code_sha256() -> str:
    root = Path(__file__).parent
    return digest({p.name: p.read_text() for p in sorted(root.glob("*.py"))})


def fold_plan(rows: list[dict], config: dict) -> tuple[list[dict], list[str]]:
    from sklearn.model_selection import StratifiedGroupKFold

    groups = [r["semantic_family"] for r in rows]
    if any(r["split"] not in ("train", "validation") for r in rows):
        raise ValueError("historical protected record reached research splitting")
    splitter = StratifiedGroupKFold(n_splits=config["n_splits"], shuffle=True, random_state=config["seed"])
    with warnings.catch_warnings(record=True) as captured:
        plan = [
            {"train": a.tolist(), "validation": b.tolist()}
            for a, b in splitter.split(list(range(len(rows))), [r["label"] for r in rows], groups)
        ]
    validate_plan(rows, plan)
    return plan, [str(w.message) for w in captured]


def validate_plan(rows: list[dict], plan: list[dict]) -> None:
    seen = []
    for fold in plan:
        train_ids, val_ids = set(fold["train"]), set(fold["validation"])
        if train_ids & val_ids or train_ids | val_ids != set(range(len(rows))):
            raise ValueError("fold record partition is invalid")
        train_families = {rows[i]["semantic_family"] for i in train_ids}
        val_families = {rows[i]["semantic_family"] for i in val_ids}
        if train_families & val_families:
            raise ValueError("family leakage in development cross-validation")
        seen.extend(val_ids)
    if sorted(seen) != list(range(len(rows))):
        raise ValueError("out-of-fold predictions must cover every eligible record exactly once")


def prepare_features(rows: list[dict], representation: str):
    import scipy.sparse as sp
    from sklearn.feature_extraction.text import TfidfVectorizer

    kinds = {"word": ["word"], "char": ["char"], "word-char": ["word", "char"]}[representation]
    blocks, matrices = [], []
    for kind in kinds:
        v = TfidfVectorizer(analyzer=kind, ngram_range=(1, 2) if kind == "word" else (3, 5), norm="l2", smooth_idf=True)
        scale = 1 / math.sqrt(len(kinds))
        matrices.append(v.fit_transform([r["text"] for r in rows]) * scale)
        blocks.append(
            {"kind": kind, "vocabulary": v.get_feature_names_out().tolist(), "idf": v.idf_.tolist(), "scale": scale}
        )
    return sp.hstack(matrices, format="csr"), blocks


def train_head(x, blocks: list[dict], targets: list[str], representation: str, objective: str) -> dict:
    from threadpoolctl import threadpool_limits

    from training.router.fit import CONFIG as HEAD_CONFIG
    from training.router.fit import head

    config = protocol()
    if any(HEAD_CONFIG[k] != config[k] for k in ("seed", "C", "class_weight", "max_iter", "solver")):
        raise ValueError("reference classifier hyperparameters changed")
    with threadpool_limits(limits=config["threads"]):
        classifier = head(x, targets)
    return {
        "architecture": "family-only" if objective == "family" else "research-direct-" + representation,
        "kind": "direct",
        "features": blocks,
        "heads": {"direct": classifier},
        "temperature": 1.0,
        "threshold": 0.0,
        "supported_labels": sorted(set(targets)),
        "training_counts": dict(Counter(targets)),
    }


def parity(model: dict, x, rows: list[dict]) -> None:
    for i, row in enumerate(rows):
        native = vector(model, row["text"])
        expected = x.getrow(i).toarray()[0]
        if any(abs(v - native.get(j, 0)) > 1e-12 for j, v in enumerate(expected)):
            raise ValueError("research export feature parity failed")


def subset_families(rows: list[dict], fraction: float, seed: int) -> list[dict]:
    by_label = defaultdict(set)
    for r in rows:
        by_label[r["label"]].add(r["semantic_family"])
    chosen = set()
    for label, groups in sorted(by_label.items()):
        ranked = sorted(groups, key=lambda g: digest([seed, label, g]))
        chosen.update(ranked[: max(1, math.ceil(len(ranked) * fraction))])
    return [r for r in rows if r["semantic_family"] in chosen]


def paired_family_bootstrap(rows: list[dict], candidate: list[dict], baseline: list[dict], config: dict) -> dict:
    deltas = defaultdict(list)
    for r, c, b in zip(rows, candidate, baseline, strict=True):
        deltas[r["semantic_family"]].append(int(c["prediction"] == r["label"]) - int(b["prediction"] == r["label"]))
    group_scores = [sum(v) / len(v) for _, v in sorted(deltas.items())]
    rng = random.Random(config["seed"])
    values = sorted(
        sum(rng.choice(group_scores) for _ in group_scores) / len(group_scores)
        for _ in range(config["paired_bootstrap"]["resamples"])
    )
    lo, hi = config["paired_bootstrap"]["interval"]
    return {
        "unit": "semantic family",
        "families": len(group_scores),
        "mean_accuracy_difference": sum(group_scores) / len(group_scores),
        "percentile_interval": [values[int(lo * (len(values) - 1))], values[int(hi * (len(values) - 1))]],
        "resamples": len(values),
        "interpretation": "descriptive development uncertainty, not a protected promotion test",
    }


def family_majority(training: list[dict], n: int) -> list[dict]:
    counts = Counter(family(r["label"]) for r in training)
    probs = {f: counts[f] / len(training) for f in ("ca", "coga", "ABSTAIN")}
    guess = max(probs, key=lambda c: (probs[c], c))
    return [
        {
            "prediction": guess,
            "argmax": guess,
            "probabilities": probs,
            "confidence": probs[guess],
            "abstention_score": probs["ABSTAIN"],
        }
        for _ in range(n)
    ]


def run(data_dir: Path, out: Path, workspace: Path, *, reproduce: bool = False) -> None:
    import importlib.metadata

    from training.phase2.dataset import check_row

    config = protocol()
    source_model = read(data_dir / "model.json")["payload"]
    labels = source_model["authoritative_capabilities"]
    canaries = read(data_dir / "canaries.json")
    rows, manifest = load_verified(
        data_dir / "dataset", source_model["dataset_sha256"], labels, canaries, read(data_dir / "quarantine.json")
    )
    for r in rows:
        check_row(r, labels, canaries)
    before = historical_snapshot(workspace / "blux")
    plan, split_warnings = fold_plan(rows, config)
    if not reproduce:
        out.mkdir(parents=True, exist_ok=True)
        with (out / "research.started").open("x") as f:
            f.write(digest(config) + "\n")
        write(
            out / "preregistered.json",
            {
                "protocol": config,
                "protocol_sha256": digest(config),
                "code_sha256": source_code_sha256(),
                "dataset_sha256": digest(manifest),
                "folds": plan,
                "warnings": split_warnings,
                "source_commit": __import__("subprocess")
                .check_output(["git", "-C", str(workspace / "blux"), "rev-parse", "HEAD"], text=True)
                .strip(),
                "dependencies": {
                    n: importlib.metadata.version(n)
                    for n in ("scikit-learn", "numpy", "scipy", "joblib", "threadpoolctl")
                },
            },
        )
    else:
        frozen = read(out / "preregistered.json")
        if (
            frozen["protocol_sha256"] != digest(config)
            or frozen["code_sha256"] != source_code_sha256()
            or frozen["dataset_sha256"] != digest(manifest)
        ):
            raise ValueError("research reproduction source/protocol/data changed")
        if any(importlib.metadata.version(n) != v for n, v in frozen["dependencies"].items()):
            raise ValueError("research reproduction dependencies changed")
        if frozen["folds"] != plan:
            raise ValueError("research folds changed")
    keys = ["majority", "keyword", "deterministic-fallback", "word", "char", "word-char", "hybrid"]
    capability_outputs = {k: [None] * len(rows) for k in keys}
    family_outputs = {k: [None] * len(rows) for k in ("majority", "keyword", "word", "char", "word-char")}
    folds, parameters, learning = [], [], []
    for number, fold in enumerate(plan):
        training = [dict(rows[i], split="train") for i in fold["train"]]
        validation = [dict(rows[i], split="validation") for i in fold["validation"]]
        # Roles are temporary DEVELOPMENT folds; immutable compiled/source split locks are not rewritten.
        fit_rows(training, "train", labels, canaries)
        fit_rows(validation, "validation", labels, canaries)
        fold_metrics = {
            "fold": number,
            "train_n": len(training),
            "validation_n": len(validation),
            "train_families": len({r["semantic_family"] for r in training}),
            "validation_families": len({r["semantic_family"] for r in validation}),
            "absent_training_capabilities": sorted(set(labels) - {r["label"] for r in training}),
            "models": {},
        }
        for baseline in ("majority", "keyword", "deterministic-fallback"):
            output = predictions(validation, training, labels, baseline)
            for i, p in zip(fold["validation"], output, strict=True):
                capability_outputs[baseline][i] = p
            if baseline in family_outputs:
                family_output = (
                    family_majority(training, len(validation)) if baseline == "majority" else family_predictions(output)
                )
                for i, p in zip(fold["validation"], family_output, strict=True):
                    family_outputs[baseline][i] = p
        for representation in config["representations"]:
            x, blocks = prepare_features(training, representation)
            for objective in ("capability", "family"):
                target = [family(r["label"]) if objective == "family" else r["label"] for r in training]
                model = train_head(x, blocks, target, representation, objective)
                parity(model, x, training)
                output = [predict(model, r["text"]) for r in validation]
                rr = [{**r, "label": family(r["label"])} for r in validation] if objective == "family" else validation
                fold_metrics["models"][objective + "-" + representation] = metrics(
                    rr, output, ["ca", "coga"] if objective == "family" else labels
                )
                destination = family_outputs if objective == "family" else capability_outputs
                for i, p in zip(fold["validation"], output, strict=True):
                    destination[representation][i] = p
                parameters.append(
                    {"fold": number, "objective": objective, "representation": representation, "model": model}
                )
            if representation == "word":
                for fraction in (0.5, 0.75):
                    subset = subset_families(training, fraction, config["seed"])
                    xx, bb = prepare_features(subset, "word")
                    fm = train_head(xx, bb, [family(r["label"]) for r in subset], "word", "family")
                    predictions_ = [predict(fm, r["text"]) for r in validation]
                    learning.append(
                        {
                            "fold": number,
                            "fraction": fraction,
                            "train_records": len(subset),
                            "train_families": len({r["semantic_family"] for r in subset}),
                            "metrics": metrics(
                                [{**r, "label": family(r["label"])} for r in validation], predictions_, ["ca", "coga"]
                            ),
                        }
                    )
                    parameters.append(
                        {"fold": number, "objective": "family-learning-curve", "fraction": fraction, "model": fm}
                    )
        for i in fold["validation"]:
            rule = capability_outputs["deterministic-fallback"][i]
            capability_outputs["hybrid"][i] = rule if rule["prediction"] != "ABSTAIN" else capability_outputs["word"][i]
        folds.append(fold_metrics)
    if any(p is None for outputs in [*capability_outputs.values(), *family_outputs.values()] for p in outputs):
        raise ValueError("missing out-of-fold predictions")
    family_rows = [{**r, "label": family(r["label"])} for r in rows]
    report = {
        "scope": "grouped DEVELOPMENT cross-validation; zero protected evidence consumed",
        "protocol_sha256": digest(config),
        "dataset_sha256": digest(manifest),
        "record_count": len(rows),
        "semantic_family_count": len({r["semantic_family"] for r in rows}),
        "split_warnings": split_warnings,
        "capability": {k: metrics(rows, v, labels) for k, v in capability_outputs.items()},
        "family": {k: metrics(family_rows, v, ["ca", "coga"]) for k, v in family_outputs.items()},
        "folds": folds,
        "learning_curve": learning,
        "paired_family_bootstrap": {
            "family": {
                k: paired_family_bootstrap(family_rows, family_outputs[k], family_outputs["keyword"], config)
                for k in config["representations"]
            },
            "capability": {
                k: paired_family_bootstrap(rows, capability_outputs[k], capability_outputs["keyword"], config)
                for k in config["representations"]
            },
        },
        "parameter_sha256": digest(parameters),
        "protected_scoring_runs": 0,
        "promotion_eligible": False,
        "legacy_family_head": (
            "word family head is the same C=4 balanced binary logistic family "
            "submodel as the hierarchical reference; not redundantly retrained"
        ),
    }
    if before != historical_snapshot(workspace / "blux"):
        raise ValueError("Phase-1 history changed")
    if reproduce:
        if digest(report) != digest(read(out / "results.json")):
            raise ValueError("research results did not reproduce")
        write(
            out / "reproduction.json",
            {
                "parameters_identical": True,
                "aggregate_predictions_metrics_identical": True,
                "parameter_sha256": report["parameter_sha256"],
                "protected_scoring_runs": 0,
            },
        )
        print("Development research parameters and results reproduced")
    else:
        write(out / "parameters.json", parameters)
        write(out / "results.json", report)
        write(out / "oof-diagnostics.json", {"capability": capability_outputs, "family": family_outputs})
        print(
            {
                "records": len(rows),
                "families": report["semantic_family_count"],
                "parameter_sha256": report["parameter_sha256"],
                "models_fit": len(parameters),
                "protected_scoring_runs": 0,
            }
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("/workspace/router-runs/blux-router-0.2"))
    parser.add_argument("--out", type=Path, default=Path("/workspace/router-runs/development-research-0.1"))
    parser.add_argument("--workspace", type=Path, default=Path("/workspace"))
    parser.add_argument("--reproduce", action="store_true")
    args = parser.parse_args()
    if args.out.resolve().is_relative_to((args.workspace / "blux").resolve()):
        raise ValueError("model parameters belong outside Git")
    run(args.data, args.out, args.workspace, reproduce=args.reproduce)


if __name__ == "__main__":
    main()
