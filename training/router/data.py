"""Sibling repository adapter with a separate pinned-toolkit subprocess per dataset."""

from __future__ import annotations

import os
import subprocess
from collections import defaultdict
from pathlib import Path

import yaml

from training.router.common import digest, write

NAMES = ("blux", "blux-coga", "blux-ca", "blux-coga-dataset", "blux-ca-dataset")
TRAINING = ("train", "validation")
PROTECTED = ("test", "adversarial", "regression", "holdout")


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def capabilities(workspace: Path) -> dict:
    from blux_ca.construction.api import CAPABILITIES as ca
    from blux_coga.reason import CAPABILITIES as coga

    result = {"ca": sorted(ca), "coga": sorted(coga)}
    for engine in result:
        doc = yaml.safe_load((workspace / f"blux-{engine}/.blux/capabilities.yaml").read_text())
        if result[engine] != sorted(c["capability_id"] for c in doc["capabilities"]):
            raise ValueError("engine source and federation descriptor disagree")
    return result


def probe(workspace: Path, name: str, mode: str, caps_path: Path) -> dict | list:
    interpreter = workspace / "setup" / name / "bin/python"
    if not interpreter.is_file():
        raise ValueError(f"pinned dataset environment absent: {interpreter}")
    env = dict(os.environ, PYTHONPATH=str(workspace / "blux"))
    result = subprocess.run(
        [str(interpreter), "-m", "training.router.probe", str(workspace / name), mode, str(caps_path)],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    import json

    return json.loads(result.stdout)


def audit(workspace: Path, out: Path) -> dict:
    caps = capabilities(workspace)
    write(out / "capabilities.json", caps)
    repos = {}
    for name in NAMES:
        root = workspace / name
        if git(root, "diff", "HEAD", "--name-only") and name != "blux":
            raise ValueError("supporting repository has source changes")
        repos[name] = {"commit": git(root, "rev-parse", "HEAD"), "branch": git(root, "branch", "--show-current")}
    datasets = {}
    fingerprints = defaultdict(set)
    for engine in ("coga", "ca"):
        name = f"blux-{engine}-dataset"
        report = probe(workspace, name, "audit", out / "capabilities.json")
        for entry in report.pop("fingerprints"):
            fingerprints[entry["sha256"]].add(entry["split"])
        datasets[name] = report
    if any(len(splits) > 1 for splits in fingerprints.values()):
        raise ValueError("instruction-only duplicate crosses splits")
    result = {
        "repositories": repos,
        "capabilities": caps,
        "datasets": datasets,
        "instruction_cross_split_duplicates": 0,
    }
    write(out / "audit.json", result)
    return result


def load_split(workspace: Path, out: Path, split: str, *, fitting: bool) -> tuple[list[dict], dict]:
    if fitting and split not in TRAINING:
        raise ValueError("protected split requested by fitting pipeline")
    if split not in TRAINING + PROTECTED:
        raise ValueError("unknown split")
    rows, counts = [], {}
    for engine in ("coga", "ca"):
        name = f"blux-{engine}-dataset"
        records = probe(workspace, name, split, out / "capabilities.json")
        counts[name] = {
            "raw": len(records),
            "eligible": sum(r["label"] is not None for r in records),
            "excluded": sum(r["label"] is None for r in records),
        }
        rows.extend(r for r in records if r["label"] is not None)
    return rows, counts


def assert_fitting(rows: list[dict], canaries: list[str]) -> None:
    if not rows:
        raise ValueError("empty fit data")
    for row in rows:
        if row["split"] not in TRAINING or row.get("canary"):
            raise ValueError("protected record at fit boundary")
        if any(canary in row["text"] for canary in canaries):
            raise ValueError("canary at fit boundary")


def code_digest() -> str:
    root = Path(__file__).parent
    return digest({p.name: p.read_text() for p in sorted(root.glob("*.py"))})


def assert_lineage(workspace: Path, out: Path, expected: dict) -> None:
    # Revalidate, including file hashes and immutable split locks, before every later phase.
    current = audit(workspace, out)
    # Later documentation/result commits are allowed; frozen training code is checked separately.
    subprocess.run(
        [
            "git",
            "-C",
            str(workspace / "blux"),
            "merge-base",
            "--is-ancestor",
            expected["repositories"]["blux"]["commit"],
            current["repositories"]["blux"]["commit"],
        ],
        check=True,
    )
    current["repositories"]["blux"]["commit"] = expected["repositories"]["blux"]["commit"]
    for name in NAMES:
        current["repositories"][name]["branch"] = expected["repositories"][name]["branch"]
    if digest(current) != digest(expected):
        raise ValueError("repository/dataset lineage changed since freeze")
