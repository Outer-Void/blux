"""HARD GATE (Stage 12): hooks inside the runtime and the model router.

- Event order for one execution is deterministic and complete.
- A veto denies; nothing a hook does can turn a policy deny into an allow.
- Hook receipts are attached to the operation (and reach BLUX System with it).
- Model routing events, credential.requested (names only) and narrowing-only route mutation.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from blux_runtime.builtin import builtin_registry, reference_policy
from blux_runtime.executor import HOOK_RECEIPTS_MEDIA_TYPE, InMemoryReceiptSink, Runtime
from blux_runtime.hooks import HookBus, HookEvent, HookResult, HookSource
from blux_runtime.hooks import catalog as hook_catalog
from blux_runtime.models import CallableBackend, ModelRouter
from blux_runtime.models import capability as model_capability
from blux_runtime.registry import CapabilityRegistry
from blux_sdk import envelopes, model_api
from tests.models.test_model_api import Script, route, table

BUILTIN = HookSource("builtin", "test")
FAKE_KEY = "sk-proj-RUNTIMEHOOKS000000000000000"
USER = {"type": "user", "id": "u"}


def tool_registry(*, device: bool = False) -> CapabilityRegistry:
    reg = builtin_registry()
    reg.register(
        {
            "contract": "blux.capability",
            "contract_version": "1.0",
            "capability_id": "fs.write",
            "version": "1.0.0",
            "summary": "Write a file (test tool).",
            "contract_versions": ["1.0"],
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "text": {"type": "string"}}},
            "output_schema": {"type": "object"},
            "side_effects": "destructive" if device else "write",
            "required_permissions": [],
            "idempotent": False,
            "timeout_ms": 5000,
            "provider": {"repository": "Outer-Void/blux", "kind": "builtin"},
            "labels": {"kind": "tool", **({"device_operation": "true"} if device else {})},
        },
        lambda inp, ctx: {"written": len(inp.get("text", ""))},
    )
    return reg


def allow_policy(*caps: str) -> dict[str, Any]:
    return {
        "contract": "blux.policy",
        "contract_version": "1.0",
        "policy_id": "test-allow",
        "default_effect": "deny",
        "rules": [
            {
                "rule_id": "allow",
                "effect": "allow",
                "capabilities": list(caps),
                "actor_types": ["user"],
                "max_side_effects": "destructive",
                "grants": [],
            }
        ],
    }


def request(cap: str, payload: dict[str, Any]) -> dict[str, Any]:
    return envelopes.execution_request(cap, payload, actor=USER, session_id="ses_01J00000000000000000000000")


class Recorder:
    def __init__(self, bus: HookBus) -> None:
        self.names: list[str] = []
        self.payloads: list[dict[str, Any]] = []
        bus.register("*", self, source=BUILTIN, hook_id="h.recorder", priority=-1000, permissions=["read_sensitive:*"])

    def __call__(self, event: HookEvent) -> None:
        self.names.append(event.name)
        self.payloads.append(event.to_document()["payload"])


def test_event_order_for_a_tool_execution_is_deterministic() -> None:
    bus = HookBus()
    rec = Recorder(bus)
    rt = Runtime(tool_registry(), allow_policy("fs.write"), hooks=bus)
    with rt:
        out = rt.execute(request("fs.write", {"path": "a.txt", "text": "hi"}))
    assert out.result["status"] == "succeeded"
    assert rec.names[0] == "runtime.starting" and rec.names[-1] == "runtime.stopped"
    execution = rec.names[rec.names.index("runtime.started") + 1 : rec.names.index("runtime.stopping")]
    assert execution == [
        "request.received",
        "request.normalized",
        "tool.before_authorize",
        "tool.authorized",
        "capability.before_execute",
        "tool.before_execute",
        "tool.after_execute",
        "capability.after_execute",
        "request.completed",
        "receipt.created",
    ]  # artifact.created fires only for handler artifacts; the hook-receipts artifact is not announced
    discovered = [
        p["capability_id"] for n, p in zip(rec.names, rec.payloads, strict=True) if n == "capability.discovered"
    ]
    assert discovered == sorted(discovered) and "fs.write" in discovered


def test_hook_receipts_are_attached_to_the_operation() -> None:
    bus = HookBus()
    sink = InMemoryReceiptSink()
    rt = Runtime(tool_registry(), allow_policy("fs.write"), hooks=bus, receipt_sink=sink)
    bus.register("tool.*", lambda e: None, source=BUILTIN, hook_id="h.audit")
    out = rt.execute(request("fs.write", {"path": "a.txt", "text": f"key {FAKE_KEY}"}))
    ((descriptor, data),) = [a for a in sink.artifacts if a[0]["media_type"] == HOOK_RECEIPTS_MEDIA_TYPE]
    assert descriptor["artifact_id"] in out.receipt["artifact_ids"]
    doc = json.loads(data)
    names = [r["name"] for r in doc["receipts"]]
    assert names[:3] == ["request.received", "request.normalized", "tool.before_authorize"]
    for r in doc["receipts"]:
        assert (
            hook_catalog.schema_errors("receipt", r) == []
            and r["context"]["operation_id"] == out.result["operation_id"]
        )
    assert FAKE_KEY not in data.decode()


def test_a_hook_veto_denies_and_names_the_hook() -> None:
    bus = HookBus()
    rec = Recorder(bus)
    bus.register(
        "tool.before_execute",
        lambda e: HookResult.veto("read-only mode"),
        source=BUILTIN,
        permissions=["veto:tool.before_execute"],
        hook_id="h.readonly",
    )
    ran: list[bool] = []
    reg = tool_registry()
    reg._entries["fs.write"] = type(reg._entries["fs.write"])(
        reg._entries["fs.write"].descriptor, lambda i, c: ran.append(True) or {}
    )
    out = Runtime(reg, allow_policy("fs.write"), hooks=bus).execute(request("fs.write", {"path": "a"}))
    err = out.result["error"]
    assert out.result["status"] == "rejected" and err["code"] == "POLICY_DENIED" and ran == []
    assert err["details"] == {
        "denied_by": "hook",
        "event": "tool.before_execute",
        "hook_id": "h.readonly",
        "reason": "read-only mode",
    }
    assert "request.failed" in rec.names and "tool.after_execute" not in rec.names


def test_hooks_cannot_turn_a_policy_deny_into_an_allow() -> None:
    bus = HookBus()
    rec = Recorder(bus)
    for name in hook_catalog.catalog():
        if hook_catalog.spec(name).vetoable:
            bus.register(name, lambda e: HookResult("allow"), source=BUILTIN, permissions=["mutate:*", "veto:*"])
    bus.register("tool.before_authorize", lambda e: {"effect": "allow"}, source=BUILTIN)
    deny_all = {**allow_policy("nothing.*"), "policy_id": "deny"}
    out = Runtime(tool_registry(), deny_all, hooks=bus).execute(request("fs.write", {"path": "a"}))
    assert out.result["error"]["code"] == "POLICY_DENIED" and out.receipt["policy_decision"]["effect"] == "deny"
    denied = rec.payloads[rec.names.index("tool.denied")]
    assert denied["denied_by"] == "policy"
    assert "security.violation" in rec.names


def test_device_operations_are_never_mutated_by_hooks() -> None:
    bus = HookBus()
    bus.register(
        "tool.before_execute",
        lambda e: HookResult.mutate(timeout_ms=1),
        source=BUILTIN,
        permissions=["mutate:*"],
        hook_id="h.tamper",
    )
    seen: list[int] = []
    reg = tool_registry(device=True)
    entry = reg._entries["fs.write"]
    reg._entries["fs.write"] = type(entry)(entry.descriptor, lambda i, c: seen.append(c.timeout_ms) or {})
    out = Runtime(reg, allow_policy("fs.write"), hooks=bus).execute(request("fs.write", {"path": "a"}))
    assert out.result["status"] == "succeeded" and seen == [5000]


def test_runtime_without_hooks_is_unchanged() -> None:
    out = Runtime(builtin_registry(), reference_policy()).execute(
        envelopes.execution_request("echo.test", {"message": "x"}, actor=USER)
    )
    assert out.result["status"] == "succeeded" and out.receipt["artifact_ids"] == []


# model router ------------------------------------------------------------------------------------------


def model_runtime(bus: HookBus, script: Script, *routes: dict[str, Any]) -> Runtime:
    reg = builtin_registry()
    model_capability.register(reg, ModelRouter(table(*routes), {"test": CallableBackend(script)}, hooks=bus))
    return Runtime(reg, model_capability.model_policy(), hooks=bus)


def test_model_events_and_credentials_by_name_only(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = HookBus()
    rec = Recorder(bus)
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    rt = model_runtime(
        bus,
        Script(main_a="PROVIDER_UNAVAILABLE"),
        dict(route("main.a"), credential_env=["OPENAI_API_KEY"]),
        route("main.b", "p2", tier="fallback"),
    )
    out = rt.execute(envelopes.execution_request("model.generate", model_api.request("blux-main", "hi"), actor=USER))
    assert out.result["status"] == "succeeded"
    model_events = [n for n in rec.names if n.startswith(("model.", "credential."))]
    assert model_events == [
        "model.before_route",
        "model.after_route",
        "model.before_call",
        "credential.requested",
        "model.after_call",
        "model.before_call",
        "model.after_call",
    ]
    created = rec.payloads[rec.names.index("artifact.created")]
    assert created["media_type"] == "application/vnd.blux.model-call+json"
    assert rec.names.index("artifact.created") < rec.names.index("receipt.created")
    cred = rec.payloads[rec.names.index("credential.requested")]
    assert cred == {"names": ["OPENAI_API_KEY"], "purpose": "model backend", "route_id": "main.a"}
    assert FAKE_KEY not in json.dumps(rec.payloads)
    calls = [p for n, p in zip(rec.names, rec.payloads, strict=True) if n == "model.after_call"]
    assert [(c["route_id"], c["outcome"], c["error_code"]) for c in calls] == [
        ("main.a", "failed", "PROVIDER_UNAVAILABLE"),
        ("main.b", "succeeded", None),
    ]


def test_hooks_may_exclude_providers_or_skip_a_route_but_never_add_one() -> None:
    bus = HookBus()
    bus.register(
        "model.before_route",
        lambda e: HookResult.mutate(exclude_providers=["p1"]),
        source=BUILTIN,
        permissions=["mutate:model.before_route.exclude_providers"],
    )
    script = Script()
    rt = model_runtime(bus, script, route("main.a"), route("main.b", "p2", tier="fallback"))
    out = rt.execute(envelopes.execution_request("model.generate", model_api.request("blux-main", "hi"), actor=USER))
    assert out.result["output"]["routing"]["route_id"] == "main.b" and [s[0] for s in script.seen] == ["main.b"]

    bus2 = HookBus()
    bus2.register(
        "model.before_route",
        lambda e: HookResult.mutate(exclude_providers=[]),
        source=BUILTIN,
        permissions=["mutate:*"],
    )
    bus2.register(
        "model.before_call",
        lambda e: HookResult.veto("not this one") if e.payload["route_id"] == "main.a" else None,
        source=BUILTIN,
        permissions=["veto:model.before_call"],
    )
    script2 = Script()
    rt2 = model_runtime(bus2, script2, route("main.a"), route("main.b", "p2", tier="fallback"))
    req = model_api.request("blux-main", "hi", routing={"exclude_providers": ["p9"]})
    out = rt2.execute(envelopes.execution_request("model.generate", req, actor=USER))
    attempts = out.result["output"]["routing"]["attempts"]
    assert attempts[0]["outcome"] == "skipped" and "vetoed by hook" in attempts[0]["reason"]
    assert [s[0] for s in script2.seen] == ["main.b"]


def test_a_vetoed_model_request_fails_explicitly() -> None:
    bus = HookBus()
    bus.register(
        "model.before_route",
        lambda e: HookResult.veto("no models today"),
        source=BUILTIN,
        permissions=["veto:model.before_route"],
        hook_id="h.nomodels",
    )
    out = model_runtime(bus, Script(), route("main.a")).execute(
        envelopes.execution_request("model.generate", model_api.request("blux-main", "hi"), actor=USER)
    )
    details = out.result["error"]["details"]
    assert (
        details["provider_code"] == "INVALID_REQUEST" and details["model_error"]["details"]["hook_id"] == "h.nomodels"
    )


@pytest.mark.skipif(
    not shutil.which("blux-system") and not Path(sys.executable).with_name("blux-system").exists(),
    reason="needs blux-system",
)
def test_hook_receipts_reach_blux_system(tmp_path: Path) -> None:
    from blux_runtime.system_sink import BluxSystemSink

    exe = (
        str(Path(sys.executable).with_name("blux-system"))
        if Path(sys.executable).with_name("blux-system").exists()
        else "blux-system"
    )
    store = tmp_path / "truth"
    assert subprocess.run([exe, "init", "--store", str(store)], capture_output=True).returncode == 0
    bus = HookBus()
    bus.register("tool.*", lambda e: None, source=BUILTIN)
    rt = Runtime(
        tool_registry(), allow_policy("fs.write"), hooks=bus, receipt_sink=BluxSystemSink(store, executable=exe)
    )
    out = rt.execute(request("fs.write", {"path": "a", "text": "hi"}))
    assert out.sink_error is None
    shown = json.loads(
        subprocess.run(
            [exe, "show", "--store", str(store), "--operation", out.result["operation_id"]],
            capture_output=True,
            text=True,
        ).stdout
    )
    assert len(shown["artifact_ids"]) == 1
