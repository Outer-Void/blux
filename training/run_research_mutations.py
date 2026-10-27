"""Verify grouped-research and consumed-evidence controls using isolated guard removals."""

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
        "proxy-protected-input",
        "../error_proxy.py",
        "test_new_development_diagnostics_reject_protected[training.error_proxy]",
        [('if any(r["split"] not in ("train", "validation") for r in rows):', "if False:")],
    ),
    (
        "family-rule-protected-input",
        "../family_diagnostics.py",
        "test_new_development_diagnostics_reject_protected[training.family_diagnostics]",
        [('if any(r["split"] not in ("train", "validation") for r in rows):', "if False:")],
    ),
    (
        "protected-research-input",
        "crossval.py",
        "test_research_never_resplits_protected",
        [('if any(r["split"] not in ("train", "validation") for r in rows):', "if False:")],
    ),
    (
        "grouped-family-leak",
        "crossval.py",
        "test_family_leak_rejected",
        [("if train_families & val_families:", "if False:")],
    ),
    (
        "missing-oof-records",
        "crossval.py",
        "test_out_of_fold_missing_rows_rejected",
        [("if sorted(seen) != list(range(len(rows))):", "if False:")],
    ),
    (
        "reused-protected-evidence",
        "ledger.py",
        "test_ledger_refuses_to_persist_reused_protected_data",
        [("if dataset_digest in consumed:", "if False:")],
    ),
    (
        "ledger-chain",
        "ledger.py",
        "test_ledger_checksum_chain",
        [('if entry["previous_sha256"] != previous or entry["experiment"]["id"] in ids:', "if False:")],
    ),
    (
        "immutable-experiment-id",
        "ledger.py",
        "test_ledger_duplicate_identity_without_protected_data",
        [
            ('if any(r["experiment"]["id"] == experiment["id"] for r in entries):', "if False:"),
            (
                'if entry["previous_sha256"] != previous or entry["experiment"]["id"] in ids:',
                'if entry["previous_sha256"] != previous:',
            ),
        ],
    ),
]


def main() -> None:
    source = Path(__file__).parent
    results = []
    for name, module, test, patches in MUTATIONS:
        with tempfile.TemporaryDirectory(prefix="blux-research-mutation-") as directory:
            root = Path(directory)
            shutil.copytree(
                source, root / "training", ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache")
            )
            path = root / "training/research" / module
            content = path.read_text()
            for old, new in patches:
                if content.count(old) != 1:
                    raise ValueError("mutation anchor changed: " + name)
                content = content.replace(old, new)
            path.write_text(content)
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "training/research/tests/test_research.py::" + test, "-q"],
                cwd=root,
                env=dict(os.environ, PYTHONPATH=str(root)),
                capture_output=True,
                text=True,
            )
            detected = result.returncode == 1 and "FAILED" in result.stdout and "ERROR collecting" not in result.stdout
            results.append({"mutation": name, "test": test, "exit_code": result.returncode, "detected": detected})
            if not detected:
                raise ValueError(str(results[-1]) + result.stdout + result.stderr)
    write(source / "research/evidence/mutations.json", {"results": results, "detected": len(results), "survived": 0})
    print(f"{len(results)} research guard removals detected; zero survived")


if __name__ == "__main__":
    main()
