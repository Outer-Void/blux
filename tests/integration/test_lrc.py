"""HARD GATE (Stage 04): BLUX -> LRC creates a disposable, reproducible fixture repository -> DAT audits it ->
BLUX System records the operation chain.

Needs the `lrc`, `dat` and `blux-system` executables and LRC and DAT checkouts (for their .blux/ and LRC's
pinned fixture). Locally the checkouts are found next to this repository; CI sets LRC_PROJECT_DIR and
DAT_PROJECT_DIR. Skips if anything is missing, unless BLUX_REQUIRE_LRC=1 (CI), in which case missing pieces
are errors.
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


LRC, DAT, SYSTEM = _exe("lrc"), _exe("dat"), _exe("blux-system")
LRC_PROJECT = Path(os.environ.get("LRC_PROJECT_DIR", ROOT.parent / "lrc"))
DAT_PROJECT = Path(os.environ.get("DAT_PROJECT_DIR", ROOT.parent / "dat"))
FIXTURE = LRC_PROJECT / "tests" / "fixtures" / "hardgate.lrc"
missing = [
    n
    for n, ok in (
        ("lrc", LRC),
        ("dat", DAT),
        ("blux-system", SYSTEM),
        ("git", shutil.which("git")),
        ("LRC .blux", (LRC_PROJECT / ".blux").is_dir()),
        ("LRC fixture", FIXTURE.is_file()),
        ("DAT .blux", (DAT_PROJECT / ".blux").is_dir()),
    )
    if not ok
]
if missing:
    if os.environ.get("BLUX_REQUIRE_LRC") == "1":
        raise RuntimeError(f"BLUX_REQUIRE_LRC=1 but missing: {missing}")
    pytest.skip(f"missing: {missing}", allow_module_level=True)
assert LRC is not None and DAT is not None and SYSTEM is not None
EXPECTED = json.loads((FIXTURE.parent / "hardgate.expected.json").read_text())

# A credential-shaped value assembled at runtime so this file never contains one.
FAKE_AWS_KEY = "AKIA" + "Q7ZK3M2N8P4R6T1V"
SERVICE = {"type": "service", "id": "ci"}


@pytest.fixture(autouse=True)
def _tools_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    dirs = dict.fromkeys(str(Path(p).parent) for p in (LRC, DAT, SYSTEM))
    monkeypatch.setenv("PATH", os.pathsep.join([*dirs, os.environ["PATH"]]))


def system(*args: str) -> tuple[int, Any]:
    proc = subprocess.run([SYSTEM, *args], capture_output=True, timeout=60)
    return proc.returncode, json.loads(proc.stdout)


@pytest.fixture
def store(tmp_path: Path) -> Path:
    path = tmp_path / "truth"
    assert system("init", "--store", str(path))[0] == 0
    return path


def runtime(store: Path) -> Runtime:
    """One registry holding both projects (their capability ids must not collide), and a policy that is the
    union of the two projects' own default policies."""
    lrc, dat = dot_blux.load(LRC_PROJECT), dot_blux.load(DAT_PROJECT)
    registry = builtin_registry()
    register_project(registry, lrc)
    register_project(registry, dat)
    policy = {
        "contract": "blux.policy",
        "contract_version": "1.0",
        "policy_id": "lrc-dat-chain",
        "default_effect": "deny",
        "rules": [*lrc.policy["rules"], *dat.policy["rules"]],
    }
    return Runtime(registry, policy, receipt_sink=BluxSystemSink(store, executable=SYSTEM))


def run(rt: Runtime, capability: str, payload: dict[str, Any], actor: dict[str, str] = SERVICE, **kw: Any) -> Any:
    outcome = rt.execute(envelopes.execution_request(capability, payload, actor=actor, **kw))
    assert outcome.sink_error is None
    return outcome


def follow_handoff(rt: Runtime, created: Any, **kw: Any) -> Any:
    handoff = created.result["output"]["handoff"]
    assert handoff["tool"] == "dat"
    return run(rt, handoff["capability_id"], handoff["input"], parent_operation_id=created.result["operation_id"], **kw)


def test_hard_gate_lrc_creates_dat_audits_system_records_the_chain(store: Path, tmp_path: Path) -> None:
    rt = runtime(store)
    session = ids.new_session_id()
    target = tmp_path / "fixture"
    created = run(rt, "repo.create", {"spec_file": str(FIXTURE), "target": str(target)}, session_id=session)
    assert created.result["status"] == "succeeded", created.result["error"]
    out = created.result["output"]
    assert out["contract"] == "lrc.result" and out["outcome"] == "created"
    # Reproducible: the same bytes and the same commit id as everywhere else (pinned in LRC's repository).
    assert out["plan_id"] == EXPECTED["plan_id"] and out["git"]["commit"] == EXPECTED["git_commit"]
    assert out["linkage"]["operation_id"] == created.result["operation_id"]
    assert created.receipt["output_sha256"] == sha256_of(out)

    audited = follow_handoff(rt, created, session_id=session)
    assert audited.result["status"] == "succeeded", audited.result["error"]
    report = audited.result["output"]
    assert report["contract"] == "dat.report" and report["outcome"] == "completed"
    assert report["gate"]["verdict"] == "pass" and report["target"]["path"] == str(target)
    assert report["linkage"]["parent_operation_id"] == created.result["operation_id"]

    code, graph = system("graph", "--store", str(store), "--session", session)
    assert code == 0
    root, child = graph["nodes"][created.result["operation_id"]], graph["nodes"][audited.result["operation_id"]]
    assert (root["capability_id"], root["own_status"], root["aggregate_status"]) == (
        "repo.create",
        "SUCCESS",
        "SUCCESS",
    )
    assert (child["capability_id"], child["own_status"]) == ("repo.audit", "SUCCESS")
    assert child["parent_operation_id"] == created.result["operation_id"]
    code, verified = system("verify", "--store", str(store))
    assert code == 0 and verified["ok"] is True and verified["records"] == 2


def test_fixture_is_reproducible_through_blux(store: Path, tmp_path: Path) -> None:
    rt = runtime(store)
    a = run(rt, "repo.create", {"spec_file": str(FIXTURE), "target": str(tmp_path / "a")}).result["output"]
    b = run(rt, "repo.create", {"spec_file": str(FIXTURE), "target": str(tmp_path / "b")}).result["output"]
    assert (a["plan_id"], a["tree_digest"], a["git"]) == (b["plan_id"], b["tree_digest"], b["git"])
    diff = subprocess.run(["diff", "-r", "-x", ".git", str(tmp_path / "a"), str(tmp_path / "b")], capture_output=True)
    assert diff.returncode == 0


def test_a_failed_dat_gate_is_a_recorded_result_not_a_failure(store: Path, tmp_path: Path) -> None:
    rt = runtime(store)
    spec = {
        "contract": "lrc.spec",
        "version": 1,
        "project": {"name": "leaky"},
        "entries": [{"path": "settings.py", "content": f'KEY = "{FAKE_AWS_KEY}"\n'}],
    }
    created = run(rt, "repo.create", {"spec": spec, "target": str(tmp_path / "leaky")})
    audited = follow_handoff(rt, created)
    assert audited.result["status"] == "succeeded" and audited.result["output"]["gate"]["verdict"] == "fail"
    assert "secrets.aws-access-key-id" in [f["rule_id"] for f in audited.result["output"]["findings"]]
    assert FAKE_AWS_KEY not in json.dumps(audited.result) + json.dumps(audited.receipt)
    code, graph = system("graph", "--store", str(store))
    assert code == 0
    assert graph["nodes"][audited.result["operation_id"]]["own_status"] == "SUCCESS"


def test_lrc_failures_are_blux_failures(store: Path, tmp_path: Path) -> None:
    rt = runtime(store)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("mine")
    exists = run(rt, "repo.create", {"spec_file": str(FIXTURE), "target": str(occupied)})
    bad = {"contract": "lrc.spec", "version": 1, "entries": [{"path": "README.md"}, {"path": "readme.md"}]}
    invalid = run(rt, "repo.create", {"spec": bad, "target": str(tmp_path / "x")})
    for outcome, code in ((exists, "TARGET_EXISTS"), (invalid, "PLAN_INVALID")):
        assert outcome.result["status"] == "failed" and outcome.result["output"] is None
        assert outcome.result["error"]["code"] == "EXECUTION_FAILED"
        assert outcome.result["error"]["details"]["provider_code"] == code
        assert outcome.receipt["error_code"] == "EXECUTION_FAILED"
    assert (occupied / "keep.txt").read_text() == "mine" and not (tmp_path / "x").exists()
    assert invalid.result["error"]["details"]["provider_details"]["issues"][0]["code"] == "case-collision"
    code, listing = system("list", "--store", str(store), "--status", "FAILURE")
    assert (code, listing["count"]) == (0, 2)


def test_destructive_migrate_is_users_only(store: Path, tmp_path: Path) -> None:
    rt = runtime(store)
    target = tmp_path / "repo"
    assert run(rt, "repo.create", {"spec_file": str(FIXTURE), "target": str(target)}).result["status"] == "succeeded"
    payload = {"spec_file": str(FIXTURE), "target": str(target), "variables": {"VERSION": "0.2.0"}}
    denied = run(rt, "repo.migrate", payload)
    assert denied.result["error"]["code"] == "POLICY_DENIED"
    allowed = run(rt, "repo.migrate", payload, actor={"type": "user", "id": "owner"})
    assert allowed.result["status"] == "succeeded", allowed.result["error"]
    assert allowed.result["output"]["outcome"] == "applied"
    code, graph = system("graph", "--store", str(store))
    assert code == 0
    assert graph["nodes"][denied.result["operation_id"]]["own_status"] == "DENIED"


def test_cli_chain(store: Path, tmp_path: Path) -> None:
    env = {**os.environ}

    def blux(*args: str) -> dict[str, Any]:
        proc = subprocess.run(
            [sys.executable, "-m", "blux_cli", "run", *args, "--actor-type", "service", "--system-store", str(store)],
            capture_output=True,
            cwd=ROOT,
            env=env,
            timeout=300,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return json.loads(proc.stdout)  # type: ignore[no-any-return]

    created = blux(
        "repo.create",
        "--project",
        str(LRC_PROJECT),
        "--input",
        json.dumps({"spec_file": str(FIXTURE), "target": str(tmp_path / "cli")}),
    )
    audited = blux(
        "repo.audit",
        "--project",
        str(DAT_PROJECT),
        "--input",
        json.dumps(created["output"]["handoff"]["input"]),
        "--parent-operation-id",
        created["operation_id"],
    )
    assert audited["output"]["gate"]["verdict"] == "pass"
    code, graph = system("graph", "--store", str(store))
    assert code == 0
    assert graph["nodes"][audited["operation_id"]]["parent_operation_id"] == created["operation_id"]
