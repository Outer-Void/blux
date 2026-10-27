"""Independent guard removals in disposable copies; no source or corpus is weakened in place."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from training.router.common import write

# name, module, targeted test, list of exact guard replacements
MUTATIONS = [
    (
        "protected-fit",
        "dataset.py",
        "test_protected_fit",
        [("if split not in DEVELOPMENT or not rows:", "if not rows:")],
    ),
    (
        "logical-split-lock",
        "dataset.py",
        "test_logical_split_lock",
        [
            (
                'if lock[r["id"]] != {"split": r["split"], "semantic_'
                'family": r["semantic_family"], "sha256": digest(r)}:',
                "if False:",
            )
        ],
    ),
    (
        "semantic-family",
        "dataset.py",
        "test_semantic_family_leak",
        [('if key in groups and groups[key] != row["split"]:', "if False:")],
    ),
    (
        "detectable-paraphrase",
        "dataset.py",
        "test_duplicate_paraphrase_leak",
        [('if rows[i]["split"] != rows[j]["split"]:', "if False:")],
    ),
    (
        "missing-provenance",
        "dataset.py",
        "test_missing_provenance",
        [("jsonschema.Draft202012Validator(record_schema(labels)).validate(row)", "pass")],
    ),
    (
        "phase1-protected-quarantine",
        "dataset.py",
        "test_phase1_protected_quarantine",
        [("if any(similar(sig, historical) for historical in quarantine):", "if False:")],
    ),
    (
        "canary",
        "dataset.py",
        "test_canary",
        [("if any(c.casefold() in canonical(row).decode().casefold() for c in canaries):", "if False:")],
    ),
    (
        "abstention-examples-removed",
        "dataset.py",
        "test_abstention_removal_detected",
        [("if nf < MIN_ABSTAIN_REASON_FAMILIES[split]:", "if False:")],
    ),
    (
        "ood-forced-capability",
        "dataset.py",
        "test_ood_cannot_be_capability",
        [
            ('if (row["label"] == ABSTAIN) != (row["reason"] in REASONS):', "if False:"),
            ('if any(p in REASONS for p in row["phenomena"]) and row["label"] != ABSTAIN:', "if False:"),
        ],
    ),
    (
        "dataset-digest",
        "dataset.py",
        "test_stale_dataset_digest",
        [("if digest(manifest) != expected_digest:", "if False:")],
    ),
    (
        "unknown-capability",
        "dataset.py",
        "test_unknown_capability",
        [("jsonschema.Draft202012Validator(record_schema(labels)).validate(row)", "pass")],
    ),
    (
        "unsupported-treated-supported",
        "artifact.py",
        "test_unsupported_not_supported",
        [('if set(labels) != {c for c, n in model["training_counts"].items() if n > 0}:', "if False:")],
    ),
    (
        "calibration-corruption",
        "artifact.py",
        "test_calibration_metadata",
        [
            (
                'if calibration["temperature"] != model["temperature"'
                '] or calibration["threshold"] != model["threshold"]:',
                "if False:",
            )
        ],
    ),
    (
        "artifact-checksum",
        "artifact.py",
        "test_artifact_checksum",
        [
            (
                'if set(envelope) != {"payload", "sha256"} or digest(envelope["payload"]) != envelope["sha256"]:',
                'if set(envelope) != {"payload", "sha256"}:',
            )
        ],
    ),
    (
        "normal-routing-block",
        "artifact.py",
        "test_development_default_fallback",
        [('if m["stage"] != "development-only" or not allow_development:', 'if m["stage"] != "development-only":')],
    ),
]


def main() -> None:
    source = Path(__file__).parent
    results = []
    for name, module, test, patches in MUTATIONS:
        with tempfile.TemporaryDirectory(prefix="router02-mutation-") as directory:
            root = Path(directory)
            shutil.copytree(
                source, root / "training", ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache")
            )
            path = root / "training/phase2" / module
            content = path.read_text()
            for old, new in patches:
                if content.count(old) != 1:
                    raise ValueError("mutation anchor changed: " + name)
                content = content.replace(old, new)
            path.write_text(content)
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "training/phase2/tests/test_phase2.py::" + test, "-q"],
                cwd=root,
                env=dict(os.environ, PYTHONPATH=str(root)),
                text=True,
                capture_output=True,
            )
            detected = result.returncode == 1 and "FAILED" in result.stdout and "ERROR collecting" not in result.stdout
            # Accept assertion failures, not unrelated collection/import failures.
            results.append({"mutation": name, "test": test, "exit_code": result.returncode, "detected": detected})
            if not detected:
                raise ValueError(str(results[-1]) + result.stdout + result.stderr)
    write(source / "phase2/evidence/mutations.json", {"results": results, "detected": len(results), "survived": 0})
    print(f"{len(results)} Phase-2 safety guard removals detected; zero survived")


if __name__ == "__main__":
    main()
