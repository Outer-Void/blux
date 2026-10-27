"""Inherit eligible source records without creating or relabeling natural-language examples."""

from __future__ import annotations

import json
from pathlib import Path

from training.phase2.dataset import signature, similar
from training.router.common import digest, read, write
from training.router.data import NAMES, audit, git, probe
from training.verify_experiment import verify

HISTORICAL_PATHS = (
    "training/router",
    "training/experiments",
    "training/ARTIFACT-POLICY.md",
    "training/tests",
    "training/run_mutations.py",
    "training/verify_experiment.py",
)


def historical_snapshot(blux: Path) -> dict:
    return {
        str(p.relative_to(blux)): digest(p.read_bytes().hex())
        for prefix in HISTORICAL_PATHS
        for p in sorted((blux / prefix).rglob("*") if (blux / prefix).is_dir() else [blux / prefix])
        if p.is_file() and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts
    }


def inventory(workspace: Path, out: Path) -> tuple[list[dict], dict, list[dict], list[str]]:
    out.mkdir(parents=True, exist_ok=True)
    before = historical_snapshot(workspace / "blux")
    phase1 = verify(workspace / "blux/training/experiments/blux-router-0.1")
    lineage = audit(workspace, out)
    quarantine, canaries, records, references = [], [], [], []
    exclusions = []
    for engine in ("coga", "ca"):
        name = f"blux-{engine}-dataset"
        root = workspace / name
        config = read(root / "dataset.json")
        lock = read(root / "splits.lock.json")["records"]
        canaries.append(config["canary"])
        # Mechanical quarantine only. Protected expected outputs never become training data or console output.
        for split in ("test", "adversarial", "regression", "holdout"):
            for line in (root / f"data/{split}.jsonl").read_text().splitlines():
                r = json.loads(line)
                quarantine.append(signature(r["task"]["instruction"]))
        for split in ("train", "validation"):
            for r in probe(workspace, name, split, out / "capabilities.json"):
                if r["label"] is None:
                    exclusions.append({"id": r["id"], "reason": "multiple-or-no-capabilities"})
                    continue
                # Quarantine is applied again after both datasets have contributed their protected signatures.
                record = {
                    "schema": "blux.routing-record",
                    "version": 1,
                    "id": name + ":" + r["id"],
                    "text": r["text"],
                    "label": r["label"],
                    "reason": "route",
                    "split": split,
                    "semantic_family": r["family"],
                    "template_family": None,
                    "paraphrase_family": r["family"],
                    "phenomena": ["ordinary"],
                    "provenance": {
                        "method": "source-record",
                        "authors": config["authors"],
                        "rights": config["license"]["spdx"],
                        "reviewer": "inherited-source-approved-provenance",
                        "approved": True,
                        "test_only": False,
                        "source": {
                            "repository": "Outer-Void/" + name,
                            "commit": lineage["repositories"][name]["commit"],
                            "path": f"data/{split}.jsonl#" + r["id"],
                            "sha256": lock[r["id"]]["content_sha256"],
                        },
                    },
                }
                records.append(record)
                references.append({k: v for k, v in record.items() if k != "text"})
    kept = []
    for r in records:
        if any(similar(signature(r["text"]), q) for q in quarantine):
            exclusions.append({"id": r["id"], "reason": "detectable-similarity-to-Phase1-protected-text"})
        else:
            kept.append(r)
    meta = {
        "schema": "blux.router02-source-audit",
        "starting_commits": {n: git(workspace / n, "rev-parse", "HEAD") for n in NAMES},
        "lineage": lineage,
        "phase1_evidence_verification": phase1,
        "phase1_preservation_sha256": digest(before),
        "phase1_files": before,
        "source_references": references,
        "exclusions": exclusions,
        "protected_signature_count": len(quarantine),
        "authoring": "no new prose or label examples authored",
    }
    if before != historical_snapshot(workspace / "blux"):
        raise ValueError("Phase-1 evidence changed during inventory")
    write(out / "source-audit.json", meta)
    write(out / "quarantine.json", quarantine)
    write(out / "canaries.json", canaries)
    return kept, meta, quarantine, canaries
