"""Verify committed aggregate evidence links; optionally verify the separately retained model artifact."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from training.router.common import digest, read
from training.router.inference import load_model


def verify(root: Path, model: Path | None = None) -> dict:
    freeze = read(root / "freeze.json")
    chain = read(root / "receipts.json")
    previous = None
    expected_order = ["test", "adversarial", "regression", "holdout"]
    if [r["phase"] for r in chain if r["phase"] in expected_order] != expected_order:
        raise ValueError("protected evaluation order mismatch")
    for entry in chain:
        if entry["previous_sha256"] != previous:
            raise ValueError("experiment receipt chain broken")
        phase = entry["phase"]
        details = entry["details"]
        if phase == "freeze" and details["freeze_sha256"] != digest(freeze):
            raise ValueError("freeze receipt mismatch")
        if phase in expected_order and details["metrics_sha256"] != digest(read(root / f"{phase}-metrics.json")):
            raise ValueError("evaluation metrics receipt mismatch")
        if phase == "reproduction" and details != read(root / "reproduction.json"):
            raise ValueError("reproduction receipt mismatch")
        if phase == "closed" and details != read(root / "gate.json"):
            raise ValueError("gate receipt mismatch")
        previous = digest(entry)
    if not any(r["phase"] == "closed" for r in chain):
        raise ValueError("experiment not closed")
    if model is not None:
        if hashlib.sha256(model.read_bytes()).hexdigest() != freeze["model_file_sha256"]:
            raise ValueError("model file checksum mismatch")
        caps = freeze["metadata"]["lineage"]["capabilities"]
        load_model(model, {c for group in caps.values() for c in group})
    return {
        "receipt_entries_verified": len(chain),
        "protected_order_verified": True,
        "model_file_verified": model is not None,
        "gate_passed": read(root / "gate.json")["passed"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=Path("training/experiments/blux-router-0.1"))
    parser.add_argument("--model", type=Path)
    args = parser.parse_args()
    print(verify(args.experiment, args.model))


if __name__ == "__main__":
    main()
