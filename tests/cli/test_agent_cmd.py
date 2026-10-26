"""`blux agent` (Stage 18): the command `ax agent` routes to."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from blux_cli import direct
from blux_cli.main import main
from blux_runtime.models import CallableBackend, ModelRouter
from tests.models.test_model_api import Script, reply, route, table


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BLUX_CONFIG", str(tmp_path / "config.toml"))
    path = tmp_path / "routes.json"
    path.write_text(json.dumps(table(route("main.a"))))
    monkeypatch.setenv("BLUX_MODEL_ROUTES", str(path))


def install(monkeypatch: pytest.MonkeyPatch, steps: list[dict[str, Any]]) -> Script:
    it = iter(steps)
    script = Script(main_a=lambda r, rt, e: reply(r, rt, text=(s := next(it)).get("text"), tool_calls=s.get("calls")))
    monkeypatch.setattr(direct, "make_router", lambda doc: ModelRouter(doc, {"test": CallableBackend(script)}))
    return script


def cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_agent_reads_inside_its_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("the answer is 42")
    install(
        monkeypatch, [{"calls": [{"id": "c", "name": "fs__read", "arguments": {"path": "notes.txt"}}]}, {"text": "42"}]
    )
    code, out, err = cli("agent", "--workspace", str(tmp_path), "--json", "what", "is", "the", "answer")
    doc = json.loads(out)
    assert (
        code == 0 and doc["text"] == "42" and doc["tool_calls"] == [{"name": "fs__read", "capability_id": "fs.read"}]
    ), err


def test_agent_refuses_dangerous_commands_and_bad_usage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "build").mkdir()
    script = install(
        monkeypatch,
        [{"calls": [{"id": "c", "name": "shell__exec", "arguments": {"command": "rm -rf build"}}]}, {"text": "done"}],
    )
    assert cli("agent", "--workspace", str(tmp_path), "--tools", "shell.exec", "clean")[0] == 0
    assert (tmp_path / "build").exists()
    assert "approval_required" in next(m for m in script.seen[1][1]["messages"] if m["role"] == "tool")["content"]
    assert cli("agent", "--tools", "device.format", "x")[0] == direct.USAGE  # not a VoidForge capability
    # Stage 22: device.* tools are VoidForge's. An agent may ask for reads; BLUX policy denies it destructive
    # device capabilities outright, and VoidForge would still need the person at the device.
    from blux_runtime.devices import voidforge

    destructive = next(r for r in voidforge.device_policy()["rules"] if r["max_side_effects"] == "destructive")
    assert "device.flash" in destructive["capabilities"] and destructive["actor_types"] == ["user"]
    assert cli("agent", "--workspace", str(tmp_path / "missing"), "x")[0] == direct.USAGE
