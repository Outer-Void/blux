"""provider.kind: subprocess (ADR-0009): protocol, failure mapping, isolation and inert loading."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from blux_runtime.executor import InMemoryReceiptSink, Outcome, Runtime
from blux_runtime.project import register_project
from blux_runtime.registry import CapabilityRegistry
from blux_runtime.subprocess_provider import SubprocessProvider, minimal_env
from blux_sdk import dot_blux, envelopes
from blux_sdk.capability import CapabilityDescriptor
from blux_sdk.errors import ErrorCode
from blux_sdk.schemas import check_envelope
from blux_sdk.serialization import sha256_of
from tests.conftest import ROOT

POLICY = {
    "contract": "blux.policy",
    "contract_version": "1.0",
    "policy_id": "test",
    "default_effect": "deny",
    "rules": [{"rule_id": "allow", "effect": "allow", "capabilities": ["fake.*"], "grants": []}],
}

# The fake provider is a Python script; its behaviour is chosen by the request input.
PROVIDER = """
import json, os, sys, time
req = json.load(sys.stdin)
mode = req["input"].get("mode", "ok")
print("diagnostic noise with a secret: hunter2", file=sys.stderr)
if mode == "ok":
    print(json.dumps({"echo": req["input"], "context": req["context"], "argv": sys.argv[1:],
                      "env": sorted(os.environ)}))
elif mode == "error":
    print(json.dumps({"error": {"code": "TARGET_NOT_FOUND", "message": "Target does not exist.",
                                "details": {"target": "x"}}}))
    sys.exit(1)
elif mode == "garbage":
    print("not json")
elif mode == "two":
    print("{}"); print("{}")
elif mode == "exit3":
    print("{}"); sys.exit(3)
elif mode == "sleep":
    open(req["input"]["pidfile"], "w").write(str(os.getpid()))
    time.sleep(30)
elif mode == "bad-output":
    print(json.dumps({"unexpected": True}))
"""


def _descriptor(entrypoint: str, *, kind: str = "subprocess", timeout_ms: int = 10_000) -> dict[str, Any]:
    return {
        "contract": "blux.capability",
        "contract_version": "1.0",
        "capability_id": "fake.run",
        "version": "1.0.0",
        "summary": "fake provider",
        "contract_versions": ["1.0"],
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object", "required": ["echo"]},
        "side_effects": "read",
        "required_permissions": [],
        "idempotent": True,
        "timeout_ms": timeout_ms,
        "provider": {"repository": "Outer-Void/fake", "kind": kind, "entrypoint": entrypoint},
    }


@pytest.fixture
def provider_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "fake-provider"
    script.write_text(f"#!{sys.executable}\n{PROVIDER}")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SUPER_SECRET_TOKEN", "do-not-pass-me")
    return bindir


def _run(
    mode: str,
    *,
    entrypoint: str = "fake-provider --flag 'a b'; rm -rf /",
    timeout_ms: int = 10_000,
    **extra: Any,
) -> Outcome:
    reg = CapabilityRegistry()
    desc = CapabilityDescriptor.from_document(_descriptor(entrypoint, timeout_ms=timeout_ms))
    reg.register(desc, SubprocessProvider(desc))
    req = envelopes.execution_request("fake.run", {"mode": mode, **extra}, actor={"type": "user", "id": "u"})
    return Runtime(reg, POLICY, receipt_sink=InMemoryReceiptSink()).execute(req)


def _error(outcome: Outcome, code: ErrorCode) -> dict[str, Any]:
    check_envelope(outcome.result, "result")
    check_envelope(outcome.receipt, "receipt")
    assert outcome.result["status"] == "failed"
    assert outcome.result["error"]["code"] == code.value
    assert outcome.receipt["error_code"] == code.value
    return dict(outcome.result["error"]["details"])


def test_success_passes_context_and_argv_without_a_shell(provider_bin: Path) -> None:
    outcome = _run("ok")
    assert outcome.result["status"] == "succeeded"
    out = outcome.result["output"]
    assert out["context"]["operation_id"] == outcome.result["operation_id"]
    assert out["context"]["capability_id"] == "fake.run"
    assert out["argv"] == ["--flag", "a b;", "rm", "-rf", "/"]  # shlex split, never a shell
    assert outcome.receipt["output_sha256"] == sha256_of(out)
    assert "SUPER_SECRET_TOKEN" not in out["env"] and "PATH" in out["env"]
    assert "hunter2" not in json.dumps(outcome.result) + json.dumps(outcome.receipt)  # stderr discarded


def test_provider_error_document(provider_bin: Path) -> None:
    details = _error(_run("error"), ErrorCode.EXECUTION_FAILED)
    assert details == {"exit_code": 1, "provider_code": "TARGET_NOT_FOUND", "provider_details": {"target": "x"}}


@pytest.mark.parametrize(
    ("mode", "code", "reason"),
    [
        ("garbage", ErrorCode.OUTPUT_VALIDATION_FAILED, "protocol_violation"),
        ("two", ErrorCode.OUTPUT_VALIDATION_FAILED, "protocol_violation"),
        ("exit3", ErrorCode.EXECUTION_FAILED, "nonzero_exit"),
    ],
)
def test_protocol_violations(provider_bin: Path, mode: str, code: ErrorCode, reason: str) -> None:
    assert _error(_run(mode), code)["reason"] == reason


def test_output_schema_still_applies(provider_bin: Path) -> None:
    _error(_run("bad-output"), ErrorCode.OUTPUT_VALIDATION_FAILED)


def test_timeout_kills_the_provider(provider_bin: Path, tmp_path: Path) -> None:
    pidfile = tmp_path / "pid"
    _error(_run("sleep", timeout_ms=1000, pidfile=str(pidfile)), ErrorCode.TIMEOUT)
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return  # killed and reaped
        time.sleep(0.1)
    pytest.fail("provider still running after its timeout")


def test_missing_executable(provider_bin: Path) -> None:
    details = _error(_run("ok", entrypoint="no-such-provider-xyz"), ErrorCode.EXECUTION_FAILED)
    assert details == {"reason": "executable_not_found", "executable": "no-such-provider-xyz"}


def test_minimal_env_allowlist() -> None:
    assert minimal_env({"PATH": "/bin", "AWS_SECRET_ACCESS_KEY": "x", "HOME": "/h"}) == {"PATH": "/bin", "HOME": "/h"}


def test_descriptor_must_be_subprocess() -> None:
    with pytest.raises(ValueError, match="not a subprocess"):
        SubprocessProvider(CapabilityDescriptor.from_document(_descriptor("x", kind="python")))


def _project(tmp_path: Path, entrypoint: str, kind: str = "subprocess") -> Path:
    import yaml

    root = tmp_path / "proj"
    (root / ".blux").mkdir(parents=True)
    (root / ".blux" / "manifest.yaml").write_text(
        'contract: blux.project\ncontract_version: "1.0"\nproject_id: fake\nname: Fake\nrole: standalone\n'
        "repository: Outer-Void/fake\ncapabilities: capabilities.yaml\npolicy: policy.yaml\n"
    )
    caps = {
        "contract": "blux.capability-set",
        "contract_version": "1.0",
        "capabilities": [_descriptor(entrypoint, kind=kind)],
    }
    (root / ".blux" / "capabilities.yaml").write_text(yaml.safe_dump(caps))
    (root / ".blux" / "policy.yaml").write_text(yaml.safe_dump(POLICY))
    return root


def test_loading_a_project_executes_nothing(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    root = _project(tmp_path, f"touch {marker}")
    reg = CapabilityRegistry()
    assert register_project(reg, dot_blux.load(root)) == ["fake.run"]
    assert not marker.exists()


def test_unsupported_provider_kind_fails_at_call(tmp_path: Path) -> None:
    reg = CapabilityRegistry()
    register_project(reg, dot_blux.load(_project(tmp_path, "pkg.mod:fn", kind="python")))
    req = envelopes.execution_request("fake.run", {}, actor={"type": "user", "id": "u"})
    outcome = Runtime(reg, POLICY).execute(req)
    assert outcome.result["error"]["details"]["reason"] == "unsupported_provider_kind"


def test_cli_run_with_project(tmp_path: Path, provider_bin: Path) -> None:
    root = _project(tmp_path, "fake-provider")
    env = {**os.environ}
    base = [sys.executable, "-m", "blux_cli"]
    proc = subprocess.run(
        [*base, "run", "fake.run", "--project", str(root), "--input", '{"mode": "ok"}'],
        capture_output=True,
        cwd=ROOT,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["output"]["echo"] == {"mode": "ok"}
    proc = subprocess.run(
        [*base, "run", "fake.run", "--project", str(root), "--input", '{"mode": "error"}'],
        capture_output=True,
        cwd=ROOT,
        env=env,
    )
    assert proc.returncode == 1 and json.loads(proc.stdout)["error"]["details"]["provider_code"] == "TARGET_NOT_FOUND"
    proc = subprocess.run([*base, "capabilities", "--project", str(root)], capture_output=True, cwd=ROOT, env=env)
    ids = [c["capability_id"] for c in json.loads(proc.stdout)["capabilities"]]
    assert ids == ["echo.test", "fake.run"]
    # The project's policy replaces the reference policy: echo.test is no longer allowed.
    proc = subprocess.run(
        [*base, "run", "echo.test", "--project", str(root), "--input", '{"message": "x"}'],
        capture_output=True,
        cwd=ROOT,
        env=env,
    )
    assert json.loads(proc.stdout)["error"]["code"] == "POLICY_DENIED"
