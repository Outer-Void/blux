"""Deliberately remove safety guards in throwaway copies and require the relevant test to fail."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from training.router.common import write

MUTATIONS = [
    (
        "protected-fit",
        "data.py",
        'if row["split"] not in TRAINING or row.get("canary"):',
        'if row.get("canary"):',
        "test_protected_fit_guard",
    ),
    (
        "text-canary",
        "data.py",
        'if any(canary in row["text"] for canary in canaries):',
        "if False:",
        "test_canary_guard",
    ),
    (
        "artifact-checksum",
        "inference.py",
        'if set(envelope) != {"payload", "sha256"} or digest(envelope["payload"]) != envelope["sha256"]:',
        'if set(envelope) != {"payload", "sha256"}:',
        "test_checksum_guard",
    ),
    (
        "manifest-digest",
        "probe.py",
        'if digest(pairs) != manifest["records_sha256"]:',
        "if False:",
        "test_authoritative_integrity_gate[manifest_digest]",
    ),
    (
        "split-lock",
        "probe.py",
        'if entry.get("status") != "active" or entry.get("split") != split:',
        "if False:",
        "test_record_gates[lock]",
    ),
]


def main() -> None:
    source = Path(__file__).resolve().parent
    results = []
    for name, filename, old, new, test in MUTATIONS:
        with tempfile.TemporaryDirectory(prefix="blux-router-mutation-") as directory:
            root = Path(directory)
            shutil.copytree(
                source, root / "training", ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache")
            )
            path = root / "training/router" / filename
            content = path.read_text()
            if content.count(old) != 1:
                raise ValueError("mutation source anchor changed: " + name)
            path.write_text(content.replace(old, new))
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "training/tests/test_router.py::" + test, "-q"],
                cwd=root,
                env=dict(os.environ, PYTHONPATH=str(root)),
                text=True,
                capture_output=True,
            )
            detected = result.returncode == 1 and "FAILED" in result.stdout and "ERROR collecting" not in result.stdout
            results.append({"mutation": name, "test": test, "exit_code": result.returncode, "detected": detected})
            if not detected:
                raise ValueError(
                    "mutation survived or infrastructure failed: " + str(results[-1]) + result.stdout + result.stderr
                )
    write(source / "experiments/mutations.json", {"results": results, "detected": len(results), "survived": 0})
    print(f"{len(results)} deliberate guard removals detected")


if __name__ == "__main__":
    main()
