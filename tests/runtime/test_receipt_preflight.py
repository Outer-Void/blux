"""OV-61 (F114, owner decision 2026-11-09): an external or destructive capability does not act when BLUX System
would refuse its receipt.

Before OV-61 every capability ran first; a refused receipt was only discovered afterwards (set aside, non-zero
exit), so `test_a_damaged_store_stops_a_destructive_capability_before_it_acts` failed: the handler ran.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from blux_runtime.builtin import builtin_registry
from blux_runtime.executor import Runtime
from blux_runtime.registry import CapabilityRegistry
from blux_runtime.system_sink import BluxSystemSink
from blux_sdk import envelopes

USER = {"type": "user", "id": "u"}


def _registry(calls: list[str]) -> CapabilityRegistry:
    reg = builtin_registry()
    for cap, effects in (("t.destroy", "destructive"), ("t.send", "external"), ("t.write", "write")):
        reg.register(
            {
                "contract": "blux.capability",
                "contract_version": "1.0",
                "capability_id": cap,
                "version": "1.0.0",
                "summary": "Test capability.",
                "contract_versions": ["1.0"],
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "side_effects": effects,
                "required_permissions": [],
                "idempotent": False,
                "timeout_ms": 5000,
                "provider": {"repository": "Outer-Void/blux", "kind": "builtin"},
                "labels": {"kind": "tool"},
            },
            lambda inp, ctx, cap=cap: calls.append(cap) or {"done": True},
        )
    return reg


POLICY = {
    "contract": "blux.policy",
    "contract_version": "1.0",
    "policy_id": "test-allow",
    "default_effect": "deny",
    "rules": [
        {
            "rule_id": "allow",
            "effect": "allow",
            "capabilities": ["t.destroy", "t.send", "t.write"],
            "actor_types": ["user"],
            "max_side_effects": "destructive",
            "grants": [],
        }
    ],
}


def _exe(tmp_path: Path, name: str, verify: str) -> str:
    """A fake blux-system: `verify` prints the given document; `ingest` accepts."""
    path = tmp_path / name
    path.write_text(
        "#!/bin/sh\n"
        f"case \"$1\" in verify) echo '{verify}'; exit 1;;\n"
        '  ingest) cat >/dev/null; echo \'{"status":"stored"}\';;\nesac\n'
    )
    path.chmod(0o755)
    return str(path)


def _run(sink: Any, cap: str, calls: list[str]) -> dict[str, Any]:
    rt = Runtime(_registry(calls), POLICY, receipt_sink=sink)
    return rt.execute(envelopes.execution_request(cap, {}, actor=USER)).result


@pytest.mark.parametrize("cap", ["t.destroy", "t.send"])
def test_a_refusing_store_stops_high_risk_capabilities_before_they_act(tmp_path: Path, cap: str) -> None:
    calls: list[str] = []
    exe = _exe(tmp_path, "corrupt", '{"code":"STORE_CORRUPT","message":"bad marker"}')
    result = _run(BluxSystemSink(tmp_path / "s", executable=exe), cap, calls)
    assert calls == []
    assert result["status"] == "failed"
    err = result["error"]
    assert err["code"] == "EXECUTION_FAILED"
    assert err["details"]["reason"] == "receipt_store_preflight"
    assert err["details"]["sink"]["code"] == "STORE_CORRUPT"


def test_a_failed_verification_also_stops_them(tmp_path: Path) -> None:
    calls: list[str] = []
    exe = _exe(tmp_path, "broken", '{"ok":false,"problems":["payload hash mismatch"]}')
    result = _run(BluxSystemSink(tmp_path / "s", executable=exe), "t.destroy", calls)
    assert calls == [] and result["error"]["details"]["sink"]["code"] == "STORE_VERIFY_FAILED"


def test_write_capabilities_are_not_preflighted(tmp_path: Path) -> None:
    calls: list[str] = []
    exe = _exe(tmp_path, "corrupt", '{"code":"STORE_CORRUPT","message":"bad marker"}')
    result = _run(BluxSystemSink(tmp_path / "s", executable=exe), "t.write", calls)
    assert calls == ["t.write"] and result["status"] == "succeeded"


@pytest.mark.parametrize(
    "verify", ['{"code":"STORE_LOCKED","message":"busy"}', '{"code":"STORE_NOT_FOUND","message":"no store"}']
)
def test_an_unavailable_store_still_runs_and_spools(tmp_path: Path, verify: str) -> None:
    calls: list[str] = []
    result = _run(BluxSystemSink(tmp_path / "s", executable=_exe(tmp_path, "busy", verify)), "t.destroy", calls)
    assert calls == ["t.destroy"] and result["status"] == "succeeded"


def test_a_missing_executable_still_runs_and_spools(tmp_path: Path) -> None:
    calls: list[str] = []
    sink = BluxSystemSink(tmp_path / "s", executable=str(tmp_path / "no-such-blux-system"))
    rt = Runtime(_registry(calls), POLICY, receipt_sink=sink)
    outcome = rt.execute(envelopes.execution_request("t.destroy", {}, actor=USER))
    assert calls == ["t.destroy"] and outcome.result["status"] == "succeeded"
    assert outcome.sink_error is not None and outcome.sink_error["spooled"] is True


def _real_blux_system() -> str | None:
    local = Path(sys.executable).with_name("blux-system")
    return str(local) if local.exists() else shutil.which("blux-system")


NO_SYSTEM = _real_blux_system() is None and os.environ.get("BLUX_REQUIRE_SYSTEM") != "1"


@pytest.mark.skipif(NO_SYSTEM, reason="no blux-system")
def test_a_damaged_store_stops_a_destructive_capability_before_it_acts(tmp_path: Path) -> None:
    exe = _real_blux_system()
    assert exe is not None
    store = tmp_path / "store"
    subprocess.run([exe, "init", "--store", str(store)], check=True, capture_output=True)
    calls: list[str] = []
    sink = BluxSystemSink(store, executable=exe)
    assert _run(sink, "t.write", calls)["status"] == "succeeded"  # a receipt is now in the store
    # Damage one stored record: verification now fails.
    damaged = False
    for f in sorted(p for p in store.rglob("*") if p.is_file() and p.suffix in (".json", ".jsonl")):
        text = f.read_text()
        if '"status"' in text and "succeeded" in text:
            f.write_text(text.replace("succeeded", "SUCCEEDED", 1))
            damaged = True
            break
    assert damaged
    verify = subprocess.run([exe, "verify", "--store", str(store)], capture_output=True, text=True)
    assert verify.returncode != 0, verify.stdout
    result = _run(sink, "t.destroy", calls)
    assert calls == ["t.write"]  # the destructive handler never ran
    assert result["error"]["details"]["reason"] == "receipt_store_preflight"
    assert json.dumps(result["error"]["details"]["sink"])
