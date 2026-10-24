"""HARD GATE (Stage 02): echo.test executes in BLUX, BLUX System stores the receipt and rebuilds the graph.

Runs against the real `blux-system` CLI. Skips if it is not installed, unless BLUX_REQUIRE_SYSTEM=1 (CI).
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

from blux_runtime.builtin import builtin_registry, reference_policy
from blux_runtime.executor import Runtime
from blux_runtime.system_sink import BluxSystemSink
from blux_sdk import envelopes, ids
from tests.conftest import ROOT


def _find() -> str | None:
    local = Path(sys.executable).with_name("blux-system")
    return str(local) if local.exists() else shutil.which("blux-system")


EXE = _find()
if EXE is None:
    if os.environ.get("BLUX_REQUIRE_SYSTEM") == "1":
        raise RuntimeError("BLUX_REQUIRE_SYSTEM=1 but blux-system is not installed")
    pytest.skip("blux-system not installed", allow_module_level=True)
assert EXE is not None


def system(*args: str, stdin: bytes | None = None) -> tuple[int, Any]:
    proc = subprocess.run([EXE, *args], input=stdin, capture_output=True, timeout=60)
    assert proc.stdout.count(b"\n") == 1, proc.stdout
    return proc.returncode, json.loads(proc.stdout)


@pytest.fixture
def store(tmp_path: Path) -> Path:
    path = tmp_path / "truth"
    assert system("init", "--store", str(path))[0] == 0
    return path


def runtime(store: Path, policy: dict[str, Any] | None = None) -> Runtime:
    return Runtime(builtin_registry(), policy or reference_policy(), receipt_sink=BluxSystemSink(store, executable=EXE))


def run(rt: Runtime, message: str = "hello", **kw: Any) -> Any:
    return rt.execute(
        envelopes.execution_request("echo.test", {"message": message}, actor={"type": "user", "id": "gate"}, **kw)
    )


def test_hard_gate_echo_receipt_stored_and_graph_reconstructed(store: Path) -> None:
    rt = runtime(store)
    session = ids.new_session_id()
    parent = run(rt, "parent", session_id=session, project_id="blux")
    child = run(rt, "child", session_id=session, project_id="blux", parent_operation_id=parent.result["operation_id"])
    for o in (parent, child):
        assert o.sink_error is None
        assert o.result["status"] == "succeeded"

    code, stored = system("show", "--store", str(store), "--receipt", parent.receipt["receipt_id"])
    assert code == 0 and stored == parent.receipt  # stored byte-for-byte equal (nothing to redact)

    code, graph = system("graph", "--store", str(store), "--session", session)
    assert code == 0
    p_op, c_op = parent.result["operation_id"], child.result["operation_id"]
    assert graph["roots"] == [p_op]
    assert graph["edges"] == [[p_op, c_op]]
    assert graph["nodes"][p_op]["children"] == [c_op]
    assert graph["nodes"][p_op]["capability_id"] == "echo.test"
    assert graph["nodes"][p_op]["aggregate_status"] == "SUCCESS"
    assert graph["nodes"][c_op]["receipt_id"] == child.receipt["receipt_id"]

    code, verify = system("verify", "--store", str(store))
    assert code == 0 and verify == {
        "ok": True,
        "records": 2,
        "head": verify["head"],
        "problems": [],
        "torn_tail": False,
    }

    code, replay = system(
        "replay-check", "--store", str(store), c_op, "--output", "-", stdin=json.dumps(child.result["output"]).encode()
    )
    assert code == 0 and replay["output_match"] is True


def test_failed_denied_and_cancelled_operations(store: Path) -> None:
    rt = runtime(store)
    root = run(rt, "root")
    root_op = root.result["operation_id"]
    bad = rt.execute(b'{"contract": "blux.execution", "contract_version": "2.0"}')
    assert bad.result["status"] == "rejected" and bad.sink_error is None
    deny_rt = runtime(store, {**reference_policy(), "rules": []})
    denied = run(deny_rt, "nope", parent_operation_id=root_op)
    assert denied.result["error"]["code"] == "POLICY_DENIED"
    cancelled_op = ids.new_operation_id()
    assert (
        system("cancel", "--store", str(store), cancelled_op, "--reason", "user aborted", "--parent", root_op)[0] == 0
    )

    code, graph = system("graph", "--store", str(store))
    assert code == 0
    n = graph["nodes"]
    assert n[bad.result["operation_id"]]["own_status"] == "FAILURE"
    assert n[bad.result["operation_id"]]["error_code"] == "UNSUPPORTED_CONTRACT_VERSION"
    assert n[denied.result["operation_id"]]["own_status"] == "DENIED"
    assert n[cancelled_op]["own_status"] == "CANCELLED"
    assert n[root_op]["own_status"] == "SUCCESS"
    assert n[root_op]["aggregate_status"] == "PARTIAL"
    assert set(n[root_op]["children"]) == {denied.result["operation_id"], cancelled_op}

    code, listing = system("list", "--store", str(store), "--status", "DENIED")
    assert (code, listing["count"]) == (0, 1)


def test_secrets_never_reach_the_store(store: Path) -> None:
    secret = "sk-proj-" + "S3cr3tS3cr3tS3cr3tS3cr3t"
    rt = runtime(store)
    # Inputs are only hashed into receipts; an actor id that looks like a credential is redacted.
    outcome = rt.execute(
        envelopes.execution_request("echo.test", {"message": secret}, actor={"type": "user", "id": secret})
    )
    assert outcome.result["output"]["message"] == secret  # the caller still gets its result
    assert outcome.sink_error is None
    log = (store / "log.jsonl").read_bytes()
    assert secret.encode() not in log
    code, stored = system("show", "--store", str(store), "--operation", outcome.result["operation_id"])
    assert code == 0
    assert stored["actor"]["id"] == "[REDACTED:openai_key]"
    assert stored["input_sha256"] == outcome.receipt["input_sha256"]


def test_unavailable_store_spools_and_flushes(tmp_path: Path) -> None:
    missing = tmp_path / "not-yet"
    sink = BluxSystemSink(missing, executable=EXE)
    rt = Runtime(builtin_registry(), reference_policy(), receipt_sink=sink)
    outcome = run(rt, "offline")
    assert outcome.result["status"] == "succeeded"  # result is never lost
    assert outcome.sink_error is not None
    assert outcome.sink_error["code"] == "STORE_NOT_FOUND" and outcome.sink_error["spooled"] is True
    assert sink.outbox.exists()
    assert system("init", "--store", str(missing))[0] == 0
    assert sink.flush() == {"sent": 1, "remaining": 0, "refused": 0}
    assert not sink.outbox.exists()
    code, stored = system("show", "--store", str(missing), "--receipt", outcome.receipt["receipt_id"])
    assert code == 0 and stored == outcome.receipt


def test_missing_executable_spools(tmp_path: Path) -> None:
    sink = BluxSystemSink(tmp_path / "s", executable=str(tmp_path / "no-such-blux-system"))
    outcome = Runtime(builtin_registry(), reference_policy(), receipt_sink=sink).execute(
        envelopes.execution_request("echo.test", {"message": "x"}, actor={"type": "user", "id": "u"})
    )
    assert outcome.sink_error is not None
    assert outcome.sink_error["code"] == "SINK_UNAVAILABLE" and outcome.sink_error["spooled"] is True


def test_cli_run_with_system_store(store: Path) -> None:
    env = {**os.environ, "PATH": f"{Path(EXE).parent}{os.pathsep}{os.environ.get('PATH', '')}"}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "blux_cli",
            "run",
            "echo.test",
            "--input",
            '{"message": "cli"}',
            "--system-store",
            str(store),
        ],
        capture_output=True,
        cwd=ROOT,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    code, stored = system("show", "--store", str(store), "--receipt", result["receipt_id"])
    assert code == 0 and stored["operation_id"] == result["operation_id"]
    # Fail closed when the receipt cannot be recorded.
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "blux_cli",
            "run",
            "echo.test",
            "--input",
            '{"message": "x"}',
            "--system-store",
            str(store.parent / "absent"),
        ],
        capture_output=True,
        cwd=ROOT,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 1
    assert json.loads(proc.stdout)["status"] == "succeeded"
    assert b"receipt not recorded by BLUX System" in proc.stderr
