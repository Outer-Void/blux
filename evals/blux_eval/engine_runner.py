"""Replay a dataset's protected records through its pinned engines; print per-record metrics as JSON.

Runs with the dataset repository's own interpreter and working directory (its venv holds the pinned engines),
so nothing here imports BLUX. Only aggregate metrics leave this process: no protected inputs or expected
outputs are printed. Usage: python engine_runner.py SPLIT [SPLIT ...]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any


def walk(value: Any):  # type: ignore[no-untyped-def]
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)


def ca_facts(out: dict[str, Any]) -> dict[str, Any]:
    steps = out.get("steps", [])
    ops = sum(
        len(s["output"]["changeset"].get("operations", []))
        for s in steps
        if s.get("kind") == "result" and isinstance(s.get("output", {}).get("changeset"), dict)
    )
    gates = [(s["output"].get("gate") or {}).get("verdict") for s in steps if s.get("kind") == "dat-report"]
    repairs = sum(
        1 for s in steps if s.get("kind") == "result" and s.get("output", {}).get("capability") == "artifact.repair"
    )
    certified = any(d.get("certified") is True for d in walk(out))
    return {
        "steps": len(steps),
        "operations": ops,
        "dat_gates": gates,
        "repairs": repairs,
        "self_certified": certified,
        "errors": sum(1 for s in steps if s.get("kind") == "error"),
    }


def main(splits: list[str]) -> int:
    sys.path.insert(0, str(Path.cwd()))  # the dataset checkout (cwd) provides dstool
    from dstool.adapters import load
    from dstool.common import Config, load_json

    root = Path(".").resolve()
    config = Config(root, load_json(root / "dataset.json"))
    adapter = load(config.raw["adapter"])
    adapter.check_engines(config)
    engines = {e["name"]: e["version"] for e in config.raw["engines"]}
    results = []
    for split in splits:
        path = root / "data" / f"{split}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            t0 = time.perf_counter()
            c0 = time.process_time()
            crashed = None
            try:
                out = adapter.generate(record["input"])
            except Exception as exc:  # noqa: BLE001 - a crash is a measured outcome
                out, crashed = None, type(exc).__name__
            wall, cpu = time.perf_counter() - t0, time.process_time() - c0
            valid = True
            if out is not None:
                try:
                    adapter.check_expected(out, record["record_id"])
                except Exception:  # noqa: BLE001
                    valid = False
            row = {
                "record_id": record["record_id"],
                "split": split,
                "category": record.get("category"),
                "match": out == record["expected"],
                "valid": valid and out is not None,
                "crashed": crashed,
                "rejected": bool(out is not None and adapter.rejected(out)),
                "expected_rejected": bool(adapter.rejected(record["expected"])),
                "wall_ms": round(wall * 1000, 2),
                "cpu_ms": round(cpu * 1000, 2),
            }
            if config.raw["adapter"] == "ca" and out is not None:
                row |= ca_facts(out)
                exp = ca_facts(record["expected"])
                row["extra_operations"] = max(0, row["operations"] - exp["operations"])
            results.append(row)
    print(
        json.dumps(
            {
                "dataset": config.raw["dataset_id"],
                "dataset_version": config.raw.get("version"),
                "engines": engines,
                "results": results,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
