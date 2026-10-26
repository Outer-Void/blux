"""Stage 16 gate: BLUX stays decoupled from the BLHermes fork (enforced in CI from now on)."""

from __future__ import annotations

import re
import subprocess
from importlib import util

from tests.conftest import ROOT


def _decoupling() -> object:
    spec = util.spec_from_file_location("decoupling", ROOT / "migration" / "blhermes" / "decoupling.py")
    assert spec and spec.loader
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_no_dependency_on_the_fork_in_any_tracked_file() -> None:
    report = _decoupling().scan()  # type: ignore[attr-defined]
    violations = {c: v["violations"] for c, v in report["classes"].items() if v["violations"]}
    assert violations == {}


def test_docs_never_link_into_the_fork_or_outside_the_repository() -> None:
    files = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "*.md"], capture_output=True, text=True, check=True
    ).stdout.split()
    link = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
    broken: list[str] = []
    for rel in files:
        if rel.startswith("migration/blhermes/") and rel.endswith("inventory.md"):
            continue
        text = (ROOT / rel).read_text(encoding="utf-8")
        for target in link.findall(text):
            if re.match(r"^[a-z]+:", target):
                continue  # external URL
            resolved = ((ROOT / rel).parent / target).resolve()
            if "blhermes-agent" in str(resolved) or ROOT not in [resolved, *resolved.parents]:
                broken.append(f"{rel} -> {target} (outside the repository)")
            elif not resolved.exists():
                broken.append(f"{rel} -> {target} (missing)")
    assert broken == []
