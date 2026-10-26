"""Stage 11 characterization: retained BLHermes behaviour, recorded before any migration.

Each probe (migration/blhermes/characterization/probe.py) runs the fork's own code at the inventoried
commit in a separate interpreter, isolated from the network and the real home directory. Its output must
equal the recorded golden. When a feature is ported, its BLUX test replays the same golden against the
BLUX module; a deliberate difference (for example the stricter child-environment scrub) is asserted there.

Needs the fork ($BLHERMES_SOURCE or ../blhermes-agent at the inventoried commit) and an interpreter with
migration/blhermes/characterization/requirements.txt ($BLHERMES_PYTHON); skips otherwise.
BLHERMES_RECORD=1 rewrites the goldens (only when the inventory moves to a new commit).
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from tests.migration.blhermes import GOLDEN, PROBE, fork, git, inventory

PROBES = (
    "paths",
    "approval_detection",
    "approval_session_state",
    "file_safety",
    "redaction",
    "child_env_scrub",
    "provider_registry",
    "toolsets",
    "tool_registry",
    "slash_commands",
    "session_store",
    "cron_schedules",
)
PYTHON = os.environ.get("BLHERMES_PYTHON")
FORK = fork()


def _skip_reason() -> str | None:
    if FORK is None:
        return "needs a blhermes-agent checkout ($BLHERMES_SOURCE)"
    if not PYTHON:
        return "needs $BLHERMES_PYTHON (an interpreter with characterization/requirements.txt)"
    if git(FORK, "rev-parse", "HEAD").strip() != inventory()["source"]["commit"]:
        return "the fork is not at the inventoried commit"
    return None


def test_every_probe_has_a_golden() -> None:
    assert sorted(p.stem for p in GOLDEN.glob("*.json")) == sorted(PROBES)


@pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or "")
@pytest.mark.parametrize("probe", PROBES)
def test_probe_matches_golden(probe: str) -> None:
    assert PYTHON is not None and FORK is not None
    proc = subprocess.run(
        [PYTHON, "-I", str(PROBE), "--fork", str(FORK), probe], capture_output=True, text=True, timeout=600
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    observed = json.loads(proc.stdout)
    golden = GOLDEN / f"{probe}.json"
    if os.environ.get("BLHERMES_RECORD") == "1":
        golden.write_text(json.dumps(observed, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    assert observed == json.loads(golden.read_text(encoding="utf-8"))
