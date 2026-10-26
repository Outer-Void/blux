"""Stage 22: BLUX -> VoidForge device capabilities. A fake VoidForge serves the real wire protocol."""

from __future__ import annotations

import hashlib
import json
import socket
import threading
from pathlib import Path
from typing import Any

import pytest

from blux_runtime.devices import voidforge
from blux_runtime.executor import Runtime
from blux_runtime.hooks import HookBus, HookResult, HookSource
from blux_runtime.registry import CapabilityRegistry
from blux_sdk import envelopes, schemas
from tests.conftest import ROOT

USER = {"type": "user", "id": "u"}
AGENT = {"type": "agent", "id": "a"}
SHA = "a" * 64
REQUESTED = [
    "device.list", "device.inspect", "device.logcat", "device.shell", "device.file.pull", "device.file.push",
    "device.package.list", "device.package.install", "device.backup", "device.reboot", "device.recovery",
    "device.sideload", "device.fastboot", "device.flash", "device.boot.patch", "device.root", "device.magisk.install",
]  # fmt: skip


class FakeVoidForge:
    """Answers like VoidForge: reads run; writes need the person at the device, who is not there."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.seen: list[dict[str, Any]] = []
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(path))
        self.sock.listen()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                line = conn.makefile("rb").readline()
                req = json.loads(line)
                self.seen.append(req)
                spec = {c["id"]: c for c in voidforge.catalog()["capabilities"]}[req["capability"]]
                if spec["class"] == "READ_ONLY":
                    status, code = "completed", "OK"
                elif spec["class"] is None:
                    status, code = "refused", "NOT_PROVIDED"
                else:
                    status, code = "authorization_declined", "AUTHORIZATION_DECLINED"
                resp = {
                    "protocol": voidforge.PROTOCOL,
                    "id": req["id"],
                    "capability": req["capability"],
                    "status": status,
                    "code": code,
                    "summary": "fake",
                    "details": "",
                    "operation": None,
                    "receipt": {"receipt_id": "vf-x", "journal_states": []},
                }
                conn.sendall((json.dumps(resp) + "\n").encode())

    def close(self) -> None:
        self.sock.close()


@pytest.fixture
def fake(tmp_path: Path) -> Any:
    server = FakeVoidForge(tmp_path / "vf.sock")
    yield server
    server.close()


def runtime(endpoint: str, bus: HookBus | None = None) -> Runtime:
    reg = CapabilityRegistry()
    voidforge.register(reg, endpoint=endpoint)
    return Runtime(reg, voidforge.device_policy(), hooks=bus)


def call(rt: Runtime, cap: str, payload: dict[str, Any], actor: dict[str, str] = USER) -> Any:
    return rt.execute(
        envelopes.execution_request(cap, payload, actor=actor, session_id="ses_01J00000000000000000000000")
    ).result


def test_every_requested_capability_is_registered_from_voidforges_catalog() -> None:
    reg = CapabilityRegistry()
    assert voidforge.register(reg) == REQUESTED
    by_id = {c["id"]: c for c in voidforge.catalog()["capabilities"]}
    for spec in by_id.values():
        schemas.validate("capability", voidforge.descriptor(spec))
    assert (
        by_id["device.flash"]["class"] == "DESTRUCTIVE"
        and voidforge.descriptor(by_id["device.flash"])["side_effects"] == "destructive"
    )
    assert voidforge.descriptor(by_id["device.magisk.install"])["side_effects"] == "destructive"  # PRIVILEGED
    assert voidforge.descriptor(by_id["device.list"])["side_effects"] == "read"
    assert all(voidforge.descriptor(c)["labels"]["device_operation"] == "true" for c in by_id.values())
    schemas.validate("policy", voidforge.device_policy())


def test_vendored_catalog_is_voidforges() -> None:
    vendored = (ROOT / "runtime/blux_runtime/devices/voidforge_capabilities.json").read_bytes()
    upstream = ROOT.parent / "voidforge/integration/protocol/v1/capabilities.json"
    if not upstream.exists():
        pytest.skip("voidforge checkout not present")
    assert hashlib.sha256(vendored).hexdigest() == hashlib.sha256(upstream.read_bytes()).hexdigest()


def test_reads_run_and_writes_still_need_the_person_at_voidforge(fake: FakeVoidForge) -> None:
    rt = runtime(str(fake.path))
    res = call(rt, "device.list", {})
    assert res["status"] == "succeeded" and res["output"]["receipt"]["receipt_id"] == "vf-x"
    res = call(rt, "device.flash", {"device": "dev-1", "target": "boot_a", "source": "/img", "sha256": SHA})
    assert res["status"] == "failed"
    assert res["error"]["details"]["reason"] == "voidforge_authorization_declined"
    assert res["error"]["details"]["voidforge_code"] == "AUTHORIZATION_DECLINED"
    assert call(rt, "device.boot.patch", {})["error"]["details"]["voidforge_code"] == "NOT_PROVIDED"
    assert all(not (voidforge.FORBIDDEN & set(r) | voidforge.FORBIDDEN & set(r.get("params", {}))) for r in fake.seen)
    assert {r["requester"]["kind"] for r in fake.seen} == {"blux"}


def test_blux_cannot_carry_or_inject_authorization(fake: FakeVoidForge) -> None:
    rt = runtime(str(fake.path))
    base = {"device": "dev-1", "target": "boot_a", "source": "/img", "sha256": SHA}
    assert call(rt, "device.flash", {**base, "approved": True})["status"] == "rejected"  # schema: unknown field
    assert call(rt, "device.logcat", {"params": {"grant": "x"}})["status"] == "rejected"
    # A hook holding every permission tries to add a grant to the call: device operations accept no mutation.
    bus = HookBus()
    bus.register(
        "tool.before_execute",
        lambda e: HookResult.mutate(arguments={**e.payload["arguments"], "grant": "g"}),
        source=HookSource("builtin", "elevate"),
        permissions=["mutate:*"],
    )
    rt = runtime(str(fake.path), bus)
    res = call(rt, "device.flash", base)
    assert res["error"]["details"]["voidforge_code"] == "AUTHORIZATION_DECLINED"
    assert "grant" not in fake.seen[-1]
    # Handler-level guard, independent of the schema.
    with pytest.raises(Exception, match="cannot carry authorization"):
        reg = CapabilityRegistry()
        voidforge.register(reg, endpoint=str(fake.path))
        from blux_runtime.interfaces import ExecutionContext

        ctx = ExecutionContext(operation_id="op", parent_operation_id=None, session_id=None, project_id=None,
                               actor=USER, capability_id="device.reboot", timeout_ms=1000, on_event=None,
                               cancel=threading.Event())  # fmt: skip
        reg.get("device.reboot").handler({"target": "system", "params": {"confirmed": "1"}}, ctx)


def test_blux_policy_limits_who_may_even_ask(fake: FakeVoidForge) -> None:
    rt = runtime(str(fake.path))
    assert call(rt, "device.list", {}, actor=AGENT)["status"] == "succeeded"
    res = call(rt, "device.flash", {"device": "d", "target": "boot_a", "source": "/i", "sha256": SHA}, actor=AGENT)
    assert res["status"] == "rejected" and res["error"]["code"] == "POLICY_DENIED"
    assert all(r["capability"] != "device.flash" for r in fake.seen)


def test_voidforge_disabled_fails_gracefully(tmp_path: Path) -> None:
    rt = runtime(str(tmp_path / "absent.sock"))
    for cap, payload in (("device.list", {}), ("device.reboot", {"target": "system"})):
        res = call(rt, cap, payload)
        assert res["status"] == "failed" and res["error"]["details"]["reason"] == "voidforge_unavailable"
        assert "enable local integrations" in res["error"]["message"]
