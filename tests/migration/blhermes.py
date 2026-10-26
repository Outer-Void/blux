"""Shared helpers for the Stage 11 BLHermes inventory, matrix and characterization tests."""

from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from tests.conftest import ROOT

MIGRATION = ROOT / "migration" / "blhermes"
GOLDEN = MIGRATION / "characterization" / "golden"
PROBE = MIGRATION / "characterization" / "probe.py"


@lru_cache(maxsize=1)
def inventory() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((MIGRATION / "inventory.json").read_text(encoding="utf-8"))
    return doc


@lru_cache(maxsize=1)
def matrix() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((MIGRATION / "parity-matrix.json").read_text(encoding="utf-8"))
    return doc


def fork() -> Path | None:
    """The blhermes-agent checkout: $BLHERMES_SOURCE, else a sibling ../blhermes-agent."""
    raw = os.environ.get("BLHERMES_SOURCE")
    path = Path(raw).expanduser() if raw else ROOT.parent / "blhermes-agent"
    return path if (path / ".git").exists() else None


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout


@lru_cache(maxsize=1)
def files_at_inventory_commit() -> frozenset[str]:
    path = fork()
    assert path is not None
    return frozenset(git(path, "ls-tree", "-r", "--name-only", inventory()["source"]["commit"]).splitlines())
