"""HARD GATE (Stage 03): BLUX invokes DAT through the contract, BLUX System records the result, and DAT
failures are distinguishable from audit findings.

Needs the `dat` and `blux-system` executables and a DAT checkout (for its .blux/). Locally the checkout is
found next to this repository; CI sets DAT_PROJECT_DIR. Skips if anything is missing, unless
BLUX_REQUIRE_DAT=1 (CI), in which case missing pieces are errors.
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
from blux_runtime.project import register_project
from blux_runtime.system_sink import BluxSystemSink
from blux_sdk import dot_blux, envelopes, ids
from blux_sdk.serialization import sha256_of
from tests.conftest import ROOT


def _exe(name: str) -> str | None:
    local = Path(sys.executable).with_name(name)
    return str(local) if local.exists() else shutil.which(name)


DAT = _exe("dat")
SYSTEM = _exe("blux-system")
DAT_PROJECT = Path(os.environ.get("DAT_PROJECT_DIR", ROOT.parent / "dat"))
missing = [
    n for n, ok in (("dat", DAT), ("blux-system", SYSTEM), ("DAT .blux", (DAT_PROJECT / ".blux").is_dir())) if not ok
]
if missing:
    if os.environ.get("BLUX_REQUIRE_DAT") == "1":
        raise RuntimeError(f"BLUX_REQUIRE_DAT=1 but missing: {missing}")
    pytest.skip(f"missing: {missing}", allow_module_level=True)
assert DAT is not None and SYSTEM is not None

# A credential-shaped value assembled at runtime so this file never contains one.
FAKE_AWS_KEY = "AKIA" + "Q7ZK3M2N8P4R6T1V"


@pytest.fixture(autouse=True)
def _dat_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", f"{Path(DAT).parent}{os.pathsep}{os.environ['PATH']}")


def system(*args: str) -> tuple[int, Any]:
    proc = subprocess.run([SYSTEM, *args], capture_output=True, timeout=60)
    return proc.returncode, json.loads(proc.stdout)


@pytest.fixture
def store(tmp_path: Path) -> Path:
    path = tmp_path / "truth"
    assert system("init", "--store", str(path))[0] == 0
    return path


def runtime(store: Path, policy: dict[str, Any] | None = None) -> Runtime:
    project = dot_blux.load(DAT_PROJECT)
    registry = builtin_registry()
    register_project(registry, project)
    return Runtime(registry, policy or project.policy, receipt_sink=BluxSystemSink(store, executable=SYSTEM))


def run(rt: Runtime, capability: str, payload: dict[str, Any], **kw: Any) -> Any:
    req = envelopes.execution_request(capability, payload, actor={"type": "service", "id": "ci"}, **kw)
    return rt.execute(req)


@pytest.fixture
def leaky_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "settings.py").write_text(f'KEY = "{FAKE_AWS_KEY}"\n')
    return repo


def test_hard_gate_dat_result_recorded_and_linked(store: Path, leaky_repo: Path) -> None:
    rt = runtime(store)
    session = ids.new_session_id()
    outcome = run(rt, "repo.security", {"target": str(leaky_repo)}, session_id=session, project_id="dat")
    assert outcome.sink_error is None
    result, receipt = outcome.result, outcome.receipt
    assert result["status"] == "succeeded"
    report = result["output"]
    assert report["contract"] == "dat.report" and report["outcome"] == "completed"
    # A failed gate is a successful audit with a negative verdict.
    assert report["gate"]["verdict"] == "fail"
    assert [f["rule_id"] for f in report["findings"]] == ["secrets.aws-access-key-id"]
    assert FAKE_AWS_KEY not in json.dumps(report)
    # Linkage both ways: report -> operation, receipt -> report bytes.
    assert report["linkage"]["operation_id"] == result["operation_id"] == receipt["operation_id"]
    assert report["linkage"]["session_id"] == session
    assert receipt["output_sha256"] == sha256_of(report)

    code, stored = system("show", "--store", str(store), "--receipt", receipt["receipt_id"])
    assert code == 0 and stored == receipt
    code, graph = system("graph", "--store", str(store), "--session", session)
    assert code == 0
    node = graph["nodes"][result["operation_id"]]
    assert (node["capability_id"], node["own_status"]) == ("repo.security", "SUCCESS")


def test_dat_failure_is_a_blux_failure_not_a_finding(store: Path, tmp_path: Path) -> None:
    rt = runtime(store)
    ok = run(rt, "repo.quality", {"target": str(tmp_path)})
    missing = run(rt, "repo.audit", {"target": str(tmp_path / "nope")}, parent_operation_id=ok.result["operation_id"])
    bad_policy = tmp_path / "policy.json"
    bad_policy.write_text('{"contract": "dat.policy", "version": 9}')
    invalid = run(
        rt,
        "repo.audit",
        {"target": str(tmp_path), "policy": str(bad_policy)},
        parent_operation_id=ok.result["operation_id"],
    )

    assert ok.result["status"] == "succeeded" and ok.result["output"]["gate"]["verdict"] == "pass"
    for outcome, dat_code in ((missing, "TARGET_NOT_FOUND"), (invalid, "INVALID_POLICY")):
        assert outcome.result["status"] == "failed"
        assert outcome.result["output"] is None
        err = outcome.result["error"]
        assert err["code"] == "EXECUTION_FAILED" and err["details"]["provider_code"] == dat_code
        assert outcome.receipt["error_code"] == "EXECUTION_FAILED"

    code, graph = system("graph", "--store", str(store))
    assert code == 0
    nodes = graph["nodes"]
    assert nodes[missing.result["operation_id"]]["own_status"] == "FAILURE"
    assert nodes[invalid.result["operation_id"]]["own_status"] == "FAILURE"
    root = nodes[ok.result["operation_id"]]
    assert (root["own_status"], root["aggregate_status"]) == ("SUCCESS", "PARTIAL")
    code, listing = system("list", "--store", str(store), "--status", "FAILURE")
    assert (code, listing["count"]) == (0, 2)


def test_policy_denial_is_recorded(store: Path, tmp_path: Path) -> None:
    deny_agents = {
        "contract": "blux.policy",
        "contract_version": "1.0",
        "policy_id": "no-agents",
        "default_effect": "deny",
        "rules": [
            {
                "rule_id": "users-only",
                "effect": "allow",
                "capabilities": ["repo.*"],
                "actor_types": ["user"],
                "grants": ["fs:read:target"],
            }
        ],
    }
    outcome = run(runtime(store, deny_agents), "repo.audit", {"target": str(tmp_path)})
    assert outcome.result["error"]["code"] == "POLICY_DENIED"
    code, graph = system("graph", "--store", str(store))
    assert code == 0 and graph["nodes"][outcome.result["operation_id"]]["own_status"] == "DENIED"


def test_policy_verify_and_artifact_audit(store: Path, tmp_path: Path) -> None:
    rt = runtime(store)
    policy = tmp_path / "p.json"
    policy.write_text(json.dumps({"contract": "dat.policy", "version": 1, "policy_id": "p"}))
    verified = run(rt, "policy.verify", {"policy": str(policy)})
    assert verified.result["status"] == "succeeded" and verified.result["output"]["verdict"] == "pass"
    import zipfile

    with zipfile.ZipFile(tmp_path / "evil.zip", "w") as zf:
        zf.writestr("../escape.txt", "x")
    art = run(rt, "artifact.audit", {"target": str(tmp_path / "evil.zip")})
    assert art.result["status"] == "succeeded"
    assert [f["rule_id"] for f in art.result["output"]["findings"]] == ["artifact.path-traversal"]


def test_cli_run_dat_through_blux_with_system_store(store: Path, leaky_repo: Path) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "blux_cli",
            "run",
            "repo.release",
            "--project",
            str(DAT_PROJECT),
            "--input",
            json.dumps({"target": str(leaky_repo), "use_target_policy": False}),
            "--system-store",
            str(store),
        ],
        capture_output=True,
        cwd=ROOT,
        timeout=120,
        env={**os.environ, "PATH": f"{Path(SYSTEM).parent}{os.pathsep}{os.environ['PATH']}"},
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["output"]["capability"] == "repo.release"
    code, stored = system("show", "--store", str(store), "--operation", result["operation_id"])
    assert code == 0 and stored["capability_id"] == "repo.release" and stored["status"] == "succeeded"
