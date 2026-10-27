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


class FakeSink:
    def __init__(self, store: str, fail: bool = False) -> None:
        self.store, self.fail, self.receipts = store, fail, []

    def emit(self, receipt: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("store unavailable")
        self.receipts.append(receipt)


def test_agent_records_receipts_in_the_configured_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Stage 43: `blux agent` honours system_store like `blux ask`, links tool calls to the model call that asked
    # for them, and fails closed (exit 5) when a receipt was not recorded.
    from blux_cli import agent_cmd

    (tmp_path / "notes.txt").write_text("42")
    (tmp_path / "config.toml").write_text(f'system_store = "{tmp_path / "store"}"\n')
    monkeypatch.setattr(direct, "_system_available", lambda: True)
    sinks: list[FakeSink] = []
    monkeypatch.setattr(agent_cmd, "BluxSystemSink", lambda store: sinks.append(FakeSink(store)) or sinks[-1])
    steps = [{"calls": [{"id": "c", "name": "fs__read", "arguments": {"path": "notes.txt"}}]}, {"text": "42"}]
    install(monkeypatch, steps)
    code, out, err = cli("agent", "--workspace", str(tmp_path), "--json", "answer")
    doc, receipts = json.loads(out), sinks[0].receipts
    assert code == 0, err
    assert sinks[0].store == str(tmp_path / "store")
    assert [r["operation_id"] for r in receipts] == doc["operations"]
    gen, read = receipts[0], receipts[1]
    assert read["capability_id"] == "fs.read" and read["parent_operation_id"] == gen["operation_id"]

    monkeypatch.setattr(agent_cmd, "BluxSystemSink", lambda store: FakeSink(store, fail=True))
    install(monkeypatch, steps)
    code, out, err = cli("agent", "--workspace", str(tmp_path), "--json", "answer")
    assert code == direct.NOT_RECORDED and json.loads(out)["text"] == "42" and "not recorded" in err

    monkeypatch.setattr(direct, "_system_available", lambda: False)
    assert cli("agent", "--workspace", str(tmp_path), "answer")[0] == direct.CONFIG
