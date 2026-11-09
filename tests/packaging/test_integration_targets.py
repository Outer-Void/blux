"""OV-61: the integration targets have one pin set, and CI installs and requires exactly those.

Before OV-61, ci.yml hard-coded engine commits from Stages 02-09 (blux-system ebdf84c, DAT ba58386, ...), behind
the OV-51/52 fixes, and installed BluxGPT at one commit while checking out another.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from tests.conftest import ROOT

spec = importlib.util.spec_from_file_location("integration_targets", ROOT / "tools" / "integration_targets.py")
assert spec and spec.loader
tool = importlib.util.module_from_spec(spec)
sys.modules["integration_targets"] = tool
spec.loader.exec_module(tool)


def test_ci_uses_the_lock() -> None:
    assert tool.problems(tool.load(), (ROOT / ".github" / "workflows" / "ci.yml").read_text()) == []


def test_hard_coded_pins_and_hand_set_flags_are_rejected() -> None:
    old = (
        'python -m pip install --no-deps "outervoid-dat @ git+https://github.com/Outer-Void/dat.git@ba58386"\n'
        '          BLUX_REQUIRE_DAT: "1"\n'
    )
    found = tool.problems(tool.load(), old)
    assert any("Outer-Void/dat at a hard-coded commit" in p for p in found)
    assert any("sets BLUX_REQUIRE_DAT by hand" in p for p in found)
    assert any("does not run `integration_targets.py env`" in p for p in found)


def test_every_target_is_pinned_and_required(tmp_path: Path) -> None:
    targets = tool.load()
    assert {t["name"] for t in targets} == {"blux-system", "dat", "lrc", "god", "blux-coga", "blux-ca", "blux-gpt"}
    lines = tool.env_lines(targets, tmp_path)
    for t in targets:
        assert len(t["commit"]) == 40
        flag = f"{t['require']}={'1' if tool.supported(t) else '0'}"
        assert flag in lines
        if t["project_env"]:
            assert f"{t['project_env']}={(tmp_path / t['name']).resolve()}" in lines


def test_the_integration_tests_read_the_flags_and_directories_the_lock_names() -> None:
    tests = "".join(p.read_text() for p in (ROOT / "tests" / "integration").glob("test_*.py"))
    for t in json.loads((ROOT / "integrations" / "targets.lock.json").read_text())["targets"]:
        assert t["require"] in tests, t["require"]
        if t["project_env"]:
            assert t["project_env"] in tests, t["project_env"]
