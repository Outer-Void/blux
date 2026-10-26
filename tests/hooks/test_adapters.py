"""HARD GATE (Stage 12): plugin, skill and MCP subscribers through adapters that do not own the bus."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from blux_runtime.hooks import (
    CallableTransport,
    ExternalSubscribers,
    HookBus,
    HookError,
    HookPermissionError,
    HookPolicy,
    HookSource,
    SubprocessTransport,
    mcp_manifest,
    plugin_manifest,
    skill_manifest,
)

FAKE_KEY = "sk-proj-ADAPTERHOOKS0000000000000"
GRANTS = {
    "builtin:*": ("observe:*", "veto:*", "mutate:*", "read_sensitive:*", "emit:*"),
    "plugin:audit": ("observe:tool.*", "observe:request.*"),
    "skill:guard": ("observe:tool.before_execute", "veto:tool.before_execute"),
    "mcp:narrow": ("observe:tool.before_execute", "mutate:tool.before_execute.timeout_ms"),
}


def tool_payload(**over: Any) -> dict[str, Any]:
    return {
        "tool": "fs.write",
        "side_effects": "write",
        "device_operation": False,
        "timeout_ms": 5000,
        "arguments": {"cmd": f"echo {FAKE_KEY}"},
        **over,
    }


@pytest.fixture
def bus() -> HookBus:
    return HookBus(policy=HookPolicy(GRANTS))


def test_plugin_observes_a_scrubbed_copy(bus: HookBus) -> None:
    seen: list[dict[str, Any]] = []
    ExternalSubscribers(bus).register(plugin_manifest("audit", ["tool.*"]), CallableTransport(seen.append))
    bus.emit("tool.before_execute", tool_payload())
    assert seen[0]["contract"] == "blux.hook-event" and seen[0]["name"] == "tool.before_execute"
    assert seen[0]["payload"]["arguments"] == "[sensitive: needs read_sensitive]"
    assert FAKE_KEY not in json.dumps(seen)


def test_skill_veto_and_mcp_narrowing_through_verdicts(bus: HookBus) -> None:
    subs = ExternalSubscribers(bus)
    subs.register(
        skill_manifest("guard", ["tool.before_execute"], ["veto:tool.before_execute"], priority=10),
        CallableTransport(lambda e: {"action": "veto", "reason": "skill says no"}),
    )
    em = bus.emit("tool.before_execute", tool_payload())
    assert not em.proceed and em.vetoed_by == "skill.guard"
    subs.unregister("skill.guard")
    subs.register(
        mcp_manifest("narrow", ["tool.before_execute"], ["mutate:tool.before_execute.timeout_ms"]),
        CallableTransport(lambda e: {"action": "mutate", "fields": {"timeout_ms": 200}}),
    )
    em = bus.emit("tool.before_execute", tool_payload())
    assert em.proceed and em.payload["timeout_ms"] == 200


def test_external_verdicts_cannot_allow_or_exceed_their_grant(bus: HookBus) -> None:
    subs = ExternalSubscribers(bus)
    subs.register(plugin_manifest("audit", ["tool.*"]), CallableTransport(lambda e: {"action": "allow"}))
    em = bus.emit("tool.before_execute", tool_payload())
    assert em.receipt["handlers"][0]["outcome"] == "rejected"
    with pytest.raises(HookPermissionError):
        subs.register(
            plugin_manifest("audit", ["tool.*"], ["veto:tool.before_execute"], hook_id="plugin.audit2"),
            CallableTransport(lambda e: None),
        )
    with pytest.raises(HookPermissionError):
        subs.register(plugin_manifest("stranger", ["tool.*"]), CallableTransport(lambda e: None))
    with pytest.raises(HookError, match="invalid hook registration"):
        subs.register(
            {**plugin_manifest("audit", ["tool.*"]), "source": {"kind": "builtin", "id": "audit"}},
            CallableTransport(lambda e: None),
        )  # adapters can never register BLUX-owned hooks
    with pytest.raises(HookError, match="invalid hook registration"):
        subs.register(
            plugin_manifest("audit", ["tool.*"], ["emit:tool"], hook_id="plugin.emitter"),
            CallableTransport(lambda e: None),
        )


def test_adapters_cannot_remove_blux_hooks(bus: HookBus) -> None:
    bus.register("tool.*", lambda e: None, source=HookSource("builtin", "core"), hook_id="builtin.core.guard")
    assert ExternalSubscribers(bus).unregister("builtin.core.guard") is False
    assert [r.hook_id for r in bus.registrations()] == ["builtin.core.guard"]
    assert not hasattr(ExternalSubscribers(bus), "emit")


def test_subprocess_subscriber_gets_no_credentials(
    bus: HookBus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    script = tmp_path / "sub.py"
    script.write_text(
        "import json, os, sys\n"
        "event = json.load(sys.stdin)\n"
        "leak = os.environ.get('OPENAI_API_KEY')\n"
        "print(json.dumps({'action': 'veto', 'reason': 'env:' + str(leak)}))\n"
    )
    subs = ExternalSubscribers(bus)
    subs.register(
        skill_manifest("guard", ["tool.before_execute"], ["veto:tool.before_execute"], timeout_ms=10000),
        SubprocessTransport(f"{sys.executable} {script}"),
    )
    em = bus.emit("tool.before_execute", tool_payload())
    assert not em.proceed and em.veto_reason == "env:None"


def test_a_hung_subprocess_subscriber_times_out(bus: HookBus, tmp_path: Path) -> None:
    script = tmp_path / "hang.py"
    script.write_text("import time\ntime.sleep(30)\n")
    ExternalSubscribers(bus).register(
        plugin_manifest("audit", ["tool.before_execute"], timeout_ms=300),
        SubprocessTransport(f"{sys.executable} {script}"),
    )
    em = bus.emit("tool.before_execute", tool_payload())
    assert em.proceed and em.receipt["handlers"][0]["outcome"] in ("timeout", "error")
