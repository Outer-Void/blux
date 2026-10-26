"""HARD GATE (Stage 12): BLUX Hook System v1, the bus itself.

Unit, ordering, timeout, recursion, permission, failure-isolation, redaction and receipt behaviour, plus
async handlers, the mutation surface, safe cancellation and the no-elevation / no-device-authorization rules.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from blux_runtime.hooks import (
    EventContext,
    HookBus,
    HookError,
    HookEvent,
    HookPermissionError,
    HookPolicy,
    HookResult,
    HookSource,
    event_catalog,
)
from blux_runtime.hooks import catalog as hook_catalog

BUILTIN = HookSource("builtin", "test")
PLUGIN = HookSource("plugin", "acme-audit", version="1.2.0")
FAKE_KEY = "sk-proj-HOOKTESTHOOKTESTHOOKTEST0000"
REQUIRED = {
    "runtime.starting",
    "runtime.started",
    "runtime.stopping",
    "runtime.stopped",
    "session.created",
    "session.loaded",
    "session.closed",
    "request.received",
    "request.normalized",
    "request.completed",
    "request.failed",
    "model.before_route",
    "model.after_route",
    "model.before_call",
    "model.after_call",
    "reason.before",
    "reason.after",
    "tool.before_authorize",
    "tool.authorized",
    "tool.denied",
    "tool.before_execute",
    "tool.after_execute",
    "tool.failed",
    "capability.discovered",
    "capability.before_execute",
    "capability.after_execute",
    "artifact.created",
    "artifact.modified",
    "artifact.validated",
    "receipt.created",
    "agent.spawned",
    "agent.completed",
    "agent.failed",
    "security.violation",
    "credential.requested",
    "training.eligible",
    "training.rejected",
}


class Receipts:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def __call__(self, receipt: dict[str, Any]) -> None:
        self.items.append(receipt)

    def named(self, name: str) -> list[dict[str, Any]]:
        return [r for r in self.items if r["name"] == name]


@pytest.fixture
def receipts() -> Receipts:
    return Receipts()


@pytest.fixture
def bus(receipts: Receipts) -> HookBus:
    return HookBus(receipt_sink=receipts, default_timeout_ms=500)


def tool_payload(**over: Any) -> dict[str, Any]:
    return {
        "tool": "fs.write",
        "side_effects": "write",
        "device_operation": False,
        "timeout_ms": 5000,
        "arguments": {"path": "notes.txt"},
        **over,
    }


# catalog / contract -------------------------------------------------------------------------------------


def test_catalog_has_every_required_family_typed_and_versioned() -> None:
    cat = event_catalog()
    assert set(cat) == REQUIRED
    assert {s.family for s in cat.values()} == {n.split(".")[0] for n in REQUIRED}
    assert hook_catalog.HOOKS_VERSION == "1.0"
    for s in cat.values():
        assert s.phase in ("before", "after", "notify")
        assert s.vetoable == (s.phase == "before"), s.name
        assert set(s.mutable) <= set(s.fields) and set(s.mutable.values()) <= set(hook_catalog.MUTATION_RULES)
        assert set(s.sensitive) <= set(s.fields)
    assert cat["credential.requested"].fields == ("names", "purpose", "route_id")  # names only, never values


def test_payloads_are_checked_against_the_contract(bus: HookBus) -> None:
    with pytest.raises(HookError, match="contract"):
        bus.emit("tool.authorized", {"tool": "x"})  # missing fields
    with pytest.raises(HookError, match="contract"):
        bus.emit("credential.requested", {"names": ["K"], "purpose": "p", "route_id": None, "value": FAKE_KEY})
    with pytest.raises(KeyError):
        bus.emit("tool.unknown", {})


# ordering ------------------------------------------------------------------------------------------------


def test_deterministic_ordering_by_priority_then_registration(bus: HookBus) -> None:
    seen: list[str] = []
    for name, prio in (("a", 0), ("b", 10), ("c", 0), ("d", -5), ("e", 10)):
        bus.register(
            "tool.authorized", lambda e, n=name: seen.append(n), source=BUILTIN, hook_id=f"h.{name}", priority=prio
        )
    for _ in range(3):
        bus.emit("tool.authorized", {"tool": "t", "policy_id": "p", "rule_id": None})
    assert seen == ["b", "e", "a", "c", "d"] * 3
    seqs = [r["sequence"] for r in bus.receipt_sinks[0].items]  # type: ignore[attr-defined]
    assert seqs == sorted(seqs) and len(set(seqs)) == 3


def test_external_sources_cannot_outrank_blux_guards() -> None:
    bus = HookBus(policy=HookPolicy({"plugin:*": ("observe:*",), "builtin:*": ("observe:*",)}))
    with pytest.raises(HookError, match="priority"):
        bus.register("tool.authorized", lambda e: None, source=PLUGIN, priority=500)
    bus.register("tool.authorized", lambda e: None, source=BUILTIN, priority=500)


# sync + async, timeouts ---------------------------------------------------------------------------------


def test_sync_and_async_handlers_in_emit_and_aemit(bus: HookBus) -> None:
    seen: list[str] = []

    async def async_hook(event: HookEvent) -> HookResult:
        await asyncio.sleep(0)
        seen.append(f"async:{event.name}")
        return HookResult.ok()

    bus.register("session.created", lambda e: seen.append(f"sync:{e.name}"), source=BUILTIN, priority=1)
    bus.register("session.created", async_hook, source=BUILTIN)
    payload = {"session_id": "ses_x", "client": "cli"}
    bus.emit("session.created", payload)
    asyncio.run(bus.aemit("session.created", payload))
    assert seen == ["sync:session.created", "async:session.created"] * 2


def test_timeouts_are_isolated_and_never_approve(bus: HookBus, receipts: Receipts) -> None:
    def slow(event: HookEvent) -> HookResult:
        time.sleep(2)
        return HookResult.ok()

    async def slow_async(event: HookEvent) -> HookResult:
        await asyncio.sleep(2)
        return HookResult.ok()

    after: list[str] = []
    bus.register("tool.authorized", slow, source=BUILTIN, timeout_ms=100, priority=5)
    bus.register("tool.authorized", lambda e: after.append("ran"), source=BUILTIN)
    t0 = time.monotonic()
    em = bus.emit("tool.authorized", {"tool": "t", "policy_id": "p", "rule_id": None})
    assert time.monotonic() - t0 < 1.5 and em.proceed and after == ["ran"]
    assert [h["outcome"] for h in em.receipt["handlers"]] == ["timeout", "ok"]
    # A guard with failure_policy=deny that times out denies (fails closed); it never approves.
    bus.register(
        "tool.before_execute",
        slow_async,
        source=BUILTIN,
        timeout_ms=100,
        failure_policy="deny",
        permissions=["veto:tool.before_execute"],
    )
    em = asyncio.run(bus.aemit("tool.before_execute", tool_payload()))
    assert not em.proceed and "timeout" in (em.veto_reason or "")


def test_repeated_failures_quarantine_the_hook(bus: HookBus) -> None:
    def broken(event: HookEvent) -> None:
        raise RuntimeError("boom")

    bus.register("tool.authorized", broken, source=BUILTIN, hook_id="h.broken")
    payload = {"tool": "t", "policy_id": "p", "rule_id": None}
    outcomes = [bus.emit("tool.authorized", payload).receipt["handlers"][0]["outcome"] for _ in range(5)]
    assert outcomes == ["error", "error", "error", "quarantined", "quarantined"]


# failure isolation -----------------------------------------------------------------------------------------


def test_a_failing_hook_never_breaks_the_caller_or_other_hooks(bus: HookBus) -> None:
    seen: list[str] = []

    def explode(event: HookEvent) -> None:
        raise ValueError(f"leaks {FAKE_KEY}")

    bus.register("tool.authorized", explode, source=BUILTIN, priority=9)
    bus.register("tool.authorized", lambda e: (_ for _ in ()).throw(SystemExit(3)), source=BUILTIN, priority=8)
    bus.register("tool.authorized", lambda e: seen.append("ok"), source=BUILTIN)
    em = bus.emit("tool.authorized", {"tool": "t", "policy_id": "p", "rule_id": None})
    assert em.proceed and seen == ["ok"]
    assert [h["outcome"] for h in em.receipt["handlers"]] == ["error", "error", "ok"]
    assert FAKE_KEY not in str(em.receipt)  # only the exception type is recorded


# recursion -------------------------------------------------------------------------------------------------


def test_recursion_is_bounded_and_reentry_skipped(receipts: Receipts) -> None:
    bus = HookBus(receipt_sink=receipts, max_depth=3)
    calls: list[int] = []

    def echo(event: HookEvent) -> None:
        calls.append(event.depth)
        bus.emit("artifact.validated", {"artifact_id": "art_x", "validator": "v", "verdict": "pass"})

    bus.register("artifact.validated", echo, source=BUILTIN, permissions=["emit:artifact"], hook_id="h.echo")
    em = bus.emit("artifact.validated", {"artifact_id": "art_x", "validator": "v", "verdict": "pass"})
    assert em.proceed and calls == [0]  # the hook is not re-entered for the event it is handling
    nested = [r for r in receipts.named("artifact.validated") if r["depth"] == 1]
    assert nested and nested[0]["handlers"][0]["outcome"] == "skipped"

    other = HookBus(receipt_sink=receipts, max_depth=2)
    depth: list[int] = []

    def ping(event: HookEvent) -> None:
        depth.append(event.depth)
        other.emit("agent.completed", {"agent_id": "a", "turns": 1})

    def pong(event: HookEvent) -> None:
        depth.append(event.depth)
        other.emit("agent.spawned", {"agent_id": "a", "parent_agent_id": None, "depth": 0, "model": None})

    other.register("agent.spawned", ping, source=BUILTIN, permissions=["emit:agent"])
    other.register("agent.completed", pong, source=BUILTIN, permissions=["emit:agent"])
    other.emit("agent.spawned", {"agent_id": "a", "parent_agent_id": None, "depth": 0, "model": None})
    assert depth == [0, 1]  # depth 2 is the limit: no handler runs there
    assert any(r["recursion_limited"] for r in receipts.items)


def test_a_recursion_limited_guard_does_not_proceed() -> None:
    bus = HookBus(max_depth=1)
    results: list[bool] = []

    def nested(event: HookEvent) -> None:
        results.append(
            bus.emit(
                "capability.before_execute",
                {"capability_id": "c", "version": "1", "side_effects": "none", "input_sha256": None},
            ).proceed
        )

    bus.register("session.created", nested, source=BUILTIN, permissions=["emit:capability"])
    bus.emit("session.created", {"session_id": "s", "client": None})
    assert results == [False]


def test_hooks_need_emit_permission_to_emit(bus: HookBus, receipts: Receipts) -> None:
    errors: list[str] = []

    def sneaky(event: HookEvent) -> None:
        try:
            bus.emit("tool.authorized", {"tool": "t", "policy_id": "p", "rule_id": None})
        except HookPermissionError as exc:
            errors.append(str(exc))

    bus.register("session.created", sneaky, source=BUILTIN, permissions=[], hook_id="h.sneaky")
    bus.emit("session.created", {"session_id": "s", "client": None})
    assert errors and receipts.named("security.violation")


# permissions -----------------------------------------------------------------------------------------------


def test_external_sources_get_only_what_policy_grants(receipts: Receipts) -> None:
    bus = HookBus(
        receipt_sink=receipts,
        policy=HookPolicy({"builtin:*": ("observe:*", "veto:*"), "plugin:acme-audit": ("observe:tool.*",)}),
    )
    bus.register("tool.*", lambda e: None, source=PLUGIN)
    with pytest.raises(HookPermissionError):
        bus.register("model.*", lambda e: None, source=PLUGIN)  # observe not granted
    with pytest.raises(HookPermissionError):
        bus.register("tool.before_execute", lambda e: None, source=PLUGIN, permissions=["veto:tool.before_execute"])
    with pytest.raises(HookPermissionError):
        bus.register("tool.*", lambda e: None, source=HookSource("skill", "unknown"))  # nothing granted
    with pytest.raises(HookPermissionError):
        HookBus(policy=HookPolicy({"plugin:*": ("*",)})).register(
            "tool.*", lambda e: None, source=PLUGIN, permissions=["emit:tool"]
        )  # external sources never emit, whatever the grant
    assert len(receipts.named("security.violation")) >= 3


def test_veto_requires_permission_and_a_vetoable_event(bus: HookBus, receipts: Receipts) -> None:
    bus.register("tool.before_execute", lambda e: HookResult.veto("no"), source=BUILTIN, hook_id="h.noperm")
    em = bus.emit("tool.before_execute", tool_payload())
    assert em.proceed and em.receipt["handlers"][0]["outcome"] == "not_permitted"
    bus.register("tool.authorized", lambda e: HookResult.veto("late"), source=BUILTIN, permissions=["veto:*"])
    em = bus.emit("tool.authorized", {"tool": "t", "policy_id": "p", "rule_id": None})
    assert em.proceed and em.receipt["handlers"][0]["outcome"] == "rejected"
    assert len(receipts.named("security.violation")) == 2


def test_a_veto_stops_the_event_and_later_hooks_are_skipped(bus: HookBus) -> None:
    bus.register(
        "tool.before_execute",
        lambda e: HookResult.veto(f"blocked {FAKE_KEY}"),
        source=BUILTIN,
        permissions=["veto:tool.before_execute"],
        hook_id="h.guard",
        priority=50,
    )
    bus.register("tool.before_execute", lambda e: None, source=BUILTIN, hook_id="h.after")
    em = bus.emit("tool.before_execute", tool_payload())
    assert not em.proceed and em.vetoed_by == "h.guard" and FAKE_KEY not in (em.veto_reason or "")
    assert [h["outcome"] for h in em.receipt["handlers"]] == ["veto", "skipped"]


# mutation surface, no elevation, no device authorization -------------------------------------------------------


def test_explicit_mutation_surface_only_narrows(bus: HookBus) -> None:
    bus.register(
        "tool.before_execute",
        lambda e: HookResult.mutate(timeout_ms=1000),
        source=BUILTIN,
        permissions=["mutate:tool.before_execute.timeout_ms"],
        hook_id="h.shorten",
    )
    em = bus.emit("tool.before_execute", tool_payload())
    assert em.payload["timeout_ms"] == 1000 and em.receipt["handlers"][0]["mutations"] == ["timeout_ms"]
    assert em.receipt["payload_sha256"] != em.receipt["final_payload_sha256"]
    bus.unregister("h.shorten")
    bus.register(
        "tool.before_execute",
        lambda e: HookResult.mutate(timeout_ms=99999),
        source=BUILTIN,
        permissions=["mutate:tool.before_execute.timeout_ms"],
        hook_id="h.lengthen",
    )
    em = bus.emit("tool.before_execute", tool_payload())
    assert em.payload["timeout_ms"] == 5000 and em.receipt["handlers"][0]["outcome"] == "rejected"


def test_hooks_cannot_elevate(bus: HookBus, receipts: Receipts) -> None:
    violations: list[str] = []
    bus.register("security.violation", lambda e: violations.append(e.payload["kind"]), source=BUILTIN)
    attempts = [
        HookResult.mutate(side_effects="none"),
        HookResult.mutate(tool="fs.read"),
        HookResult.mutate(arguments={"path": "/etc/shadow"}),
        HookResult("allow"),
    ]
    for i, result in enumerate(attempts):
        bus.register(
            "tool.before_execute",
            lambda e, r=result: r,
            source=BUILTIN,
            permissions=["mutate:*", "veto:*"],
            hook_id=f"h.elevate{i}",
        )
    bus.register("tool.before_execute", lambda e: {"decision": "allow"}, source=BUILTIN, hook_id="h.dict")
    em = bus.emit("tool.before_execute", tool_payload())
    assert em.payload == tool_payload() and em.proceed
    assert all(h["outcome"] == "rejected" for h in em.receipt["handlers"])
    assert violations == ["elevation", "elevation", "mutation", "protocol", "protocol"]


def test_hooks_cannot_authorize_or_alter_destructive_device_operations(bus: HookBus) -> None:
    violations: list[str] = []
    bus.register("security.violation", lambda e: violations.append(e.payload["kind"]), source=BUILTIN)
    device = tool_payload(tool="device.flash", side_effects="destructive", device_operation=True)
    bus.register(
        "tool.before_execute",
        lambda e: HookResult.mutate(timeout_ms=10),
        source=BUILTIN,
        permissions=["mutate:*"],
        hook_id="h.device",
    )
    em = bus.emit("tool.before_execute", device)
    assert em.payload == device and em.receipt["handlers"][0]["outcome"] == "rejected" and violations == ["device"]
    # The only thing a hook can do to a device operation is stop it.
    bus.register(
        "tool.before_execute",
        lambda e: HookResult.veto("no flashing"),
        source=BUILTIN,
        permissions=["veto:*"],
        hook_id="h.stop",
    )
    assert not bus.emit("tool.before_execute", device).proceed
    assert not hasattr(HookResult, "allow") and not hasattr(HookResult, "authorize")


# safe cancellation -----------------------------------------------------------------------------------------


def test_cancellation_never_counts_as_approval(bus: HookBus) -> None:
    cancel = threading.Event()

    def cancel_now(event: HookEvent) -> None:
        cancel.set()

    bus.register("tool.before_execute", cancel_now, source=BUILTIN, priority=9)
    bus.register("tool.before_execute", lambda e: None, source=BUILTIN)
    em = bus.emit("tool.before_execute", tool_payload(), cancel=cancel)
    assert em.cancelled and not em.proceed
    assert [h["outcome"] for h in em.receipt["handlers"]] == ["ok", "cancelled"]
    cancel.clear()
    bus.register("tool.authorized", cancel_now, source=BUILTIN, priority=9)
    bus.register("tool.authorized", lambda e: None, source=BUILTIN)
    em = bus.emit("tool.authorized", {"tool": "t", "policy_id": "p", "rule_id": None}, cancel=cancel)
    assert em.cancelled and em.proceed  # a notify event has nothing to approve


# immutability, redaction ---------------------------------------------------------------------------------


def test_core_metadata_and_payload_are_immutable(bus: HookBus) -> None:
    problems: list[str] = []

    def tamper(event: HookEvent) -> None:
        for attempt in (
            lambda: setattr(event, "name", "x"),
            lambda: event.payload.__setitem__("tool", "x"),  # type: ignore[index]
            lambda: event.payload["arguments"].__setitem__("path", "/etc"),  # type: ignore[index]
            lambda: setattr(event.context, "actor_id", "root"),
        ):
            try:
                attempt()
                problems.append("mutated")
            except (AttributeError, TypeError):
                pass

    bus.register("tool.before_execute", tamper, source=BUILTIN)
    em = bus.emit("tool.before_execute", tool_payload(), context=EventContext(actor_id="u"))
    assert problems == [] and em.payload == tool_payload()


def test_payloads_are_secret_safe_and_sensitive_fields_need_permission(bus: HookBus, receipts: Receipts) -> None:
    seen: dict[str, Any] = {}
    bus.register(
        "tool.before_execute",
        lambda e: seen.setdefault("plain", e.payload["arguments"]),
        source=BUILTIN,
        hook_id="h.plain",
    )
    bus.register(
        "tool.before_execute",
        lambda e: seen.setdefault("trusted", e.payload["arguments"]),
        source=BUILTIN,
        permissions=["read_sensitive:tool.before_execute"],
        hook_id="h.trusted",
    )
    em = bus.emit("tool.before_execute", tool_payload(arguments={"cmd": f"export OPENAI_API_KEY={FAKE_KEY}"}))
    assert seen["plain"] == "[sensitive: needs read_sensitive]"
    assert FAKE_KEY not in str(seen["trusted"]) and "[redacted]" in str(seen["trusted"])
    assert FAKE_KEY not in str(em.payload) and FAKE_KEY not in str(receipts.items)


# receipts -------------------------------------------------------------------------------------------------


def test_every_emission_has_a_schema_valid_audit_receipt(bus: HookBus, receipts: Receipts) -> None:
    bus.register("tool.before_execute", lambda e: None, source=BUILTIN, hook_id="h.audit")
    ctx = EventContext(operation_id="op_01J0000000000000000000000", session_id="ses_1", actor_type="user", actor_id="u")
    em = bus.emit("tool.before_execute", tool_payload(), context=ctx)
    for receipt in receipts.items:
        assert hook_catalog.schema_errors("receipt", receipt) == [], receipt
    r = em.receipt
    assert r["name"] == "tool.before_execute" and r["context"]["operation_id"] == ctx.operation_id
    assert r["handlers"] == [
        {
            "hook_id": "h.audit",
            "source": "builtin:test",
            "priority": 0,
            "outcome": "ok",
            "duration_ms": r["handlers"][0]["duration_ms"],
            "mutations": [],
            "detail": None,
        }
    ]
    assert "notes.txt" not in str(r)  # payload values never appear in receipts, only hashes
    assert hook_catalog.schema_errors("event", em.event.to_document()) == []
