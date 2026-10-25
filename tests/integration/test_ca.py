"""HARD GATE (Stage 07): CogA plan -> cA construction -> DAT validation -> bounded repair if needed ->
BLUX System receipt graph.

Needs the `coga`, `ca`, `dat` and `blux-system` executables and blux-coga, blux-ca and dat checkouts (CI:
COGA_PROJECT_DIR, CA_PROJECT_DIR, DAT_PROJECT_DIR). Skips if anything is missing, unless BLUX_REQUIRE_CA=1 (CI).
"""

from __future__ import annotations

import itertools
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


COGA, CA, DAT, SYSTEM = _exe("coga"), _exe("ca"), _exe("dat"), _exe("blux-system")
COGA_PROJECT = Path(os.environ.get("COGA_PROJECT_DIR", ROOT.parent / "blux-coga"))
CA_PROJECT = Path(os.environ.get("CA_PROJECT_DIR", ROOT.parent / "blux-ca"))
DAT_PROJECT = Path(os.environ.get("DAT_PROJECT_DIR", ROOT.parent / "dat"))
PLAN_REQUEST = COGA_PROJECT / "src" / "blux_coga" / "reason" / "evals" / "cases" / "plan-basic.json"
missing = [
    n
    for n, ok in (
        ("coga", COGA),
        ("ca", CA),
        ("dat", DAT),
        ("blux-system", SYSTEM),
        ("CogA .blux", (COGA_PROJECT / ".blux").is_dir()),
        ("cA .blux", (CA_PROJECT / ".blux").is_dir()),
        ("DAT .blux", (DAT_PROJECT / ".blux").is_dir()),
        ("CogA plan case", PLAN_REQUEST.is_file()),
    )
    if not ok
]
if missing:
    if os.environ.get("BLUX_REQUIRE_CA") == "1":
        raise RuntimeError(f"BLUX_REQUIRE_CA=1 but missing: {missing}")
    pytest.skip(f"missing: {missing}", allow_module_level=True)
assert SYSTEM is not None
SERVICE = {"type": "service", "id": "ci"}
AGENT = {"type": "agent", "id": "planner"}
BUGGY = (
    "import pdb\n\n\ndef parse(text):\n    try:\n        return int(text)\n"
    "    except:\n        pdb.set_trace()\n        return None\n"
)


@pytest.fixture(autouse=True)
def _tools_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    dirs = dict.fromkeys(str(Path(p).parent) for p in (COGA, CA, DAT, SYSTEM) if p)
    monkeypatch.setenv("PATH", os.pathsep.join([*dirs, os.environ["PATH"]]))


def system(*args: str) -> tuple[int, Any]:
    proc = subprocess.run([SYSTEM, *args], capture_output=True, timeout=60)
    return proc.returncode, json.loads(proc.stdout)


@pytest.fixture
def store(tmp_path: Path) -> Path:
    path = tmp_path / "truth"
    assert system("init", "--store", str(path))[0] == 0
    return path


class Chain:
    def __init__(self, store: Path) -> None:
        self.registry = builtin_registry()
        rules: list[dict[str, Any]] = []
        for root in (COGA_PROJECT, CA_PROJECT, DAT_PROJECT):
            project = dot_blux.load(root)
            register_project(self.registry, project)
            rules.extend(project.policy["rules"])
        policy = {
            "contract": "blux.policy",
            "contract_version": "1.0",
            "policy_id": "stage-07-chain",
            "default_effect": "deny",
            "rules": rules,
        }
        self.runtime = Runtime(self.registry, policy, receipt_sink=BluxSystemSink(store, executable=SYSTEM))

    def run(self, capability: str, payload: dict[str, Any], actor: dict[str, str] = SERVICE, **kw: Any) -> Any:
        outcome = self.runtime.execute(envelopes.execution_request(capability, payload, actor=actor, **kw))
        assert outcome.sink_error is None
        return outcome


def ok(outcome: Any) -> dict[str, Any]:
    assert outcome.result["status"] == "succeeded", outcome.result["error"]
    return outcome.result["output"]  # type: ignore[no-any-return]


def blueprint(content: str) -> dict[str, Any]:
    return {
        "units": [
            {"task": "build", "files": [{"path": "app/core.py", "content": content}]},
            {
                "task": "docs",
                "files": [
                    {
                        "path": "README.md",
                        "builtin": "markdown.readme",
                        "params": {"title": "Demo", "summary": "Built by cA from a CogA plan."},
                    }
                ],
            },
            {
                "task": "test",
                "files": [
                    {"path": "pyproject.toml", "config": {"data": {"project": {"name": "demo", "version": "0.1.0"}}}}
                ],
            },
        ]
    }


def test_hard_gate_coga_plans_ca_builds_dat_validates_ca_repairs_system_records(store: Path, tmp_path: Path) -> None:
    chain = Chain(store)
    session = ids.new_session_id()
    workspace = str(tmp_path / "build")

    # 1. CogA plans.
    planned = chain.run("reason.plan", json.loads(PLAN_REQUEST.read_text()), session_id=session)
    plan = ok(planned)
    assert plan["result"]["order"] == ["design", "build", "docs", "test", "release"]

    # 2. cA constructs from the plan, in the plan's order. It does not certify what it built.
    built = chain.run(
        "artifact.construct",
        {"workspace": workspace, "plan": plan, "blueprint": blueprint(BUGGY)},
        session_id=session,
        parent_operation_id=planned.result["operation_id"],
    )
    construction = ok(built)
    assert construction["status"] == "applied" and construction["certified"] is False
    assert construction["construction"]["plan"] == {
        "artifact_id": plan["artifact_id"],
        "input_sha256": plan["input_sha256"],
        "operation_id": planned.result["operation_id"],  # cA records which BLUX operation planned it
    }
    assert [t["task"] for t in construction["construction"]["tasks"]] == ["build", "docs", "test"]
    handoff = construction["validation"]["suggested"]
    assert (construction["validation"]["status"], handoff["capability_id"]) == ("required", "repo.quality")

    # 3. DAT validates independently, and fails the build.
    first = chain.run(
        handoff["capability_id"],
        {**handoff["input"], "fail_on": "low"},
        session_id=session,
        parent_operation_id=built.result["operation_id"],
    )
    report = ok(first)
    assert report["gate"]["verdict"] == "fail"
    assert sorted(f["rule_id"] for f in report["findings"]) == [
        "python.bare-except",
        "python.debugger",
        "python.debugger",
    ]

    # 4. cA repairs, within the default limit, from DAT's report.
    repaired = chain.run(
        "artifact.repair",
        {"workspace": workspace, "report": report},
        session_id=session,
        parent_operation_id=first.result["operation_id"],
    )
    fix = ok(repaired)
    assert fix["status"] == "applied" and fix["certified"] is False
    assert (fix["repair"]["attempt"], fix["repair"]["max_attempts"], fix["repair"]["remaining_attempts"]) == (1, 2, 1)
    assert len(fix["repair"]["repaired"]) == 3 and fix["repair"]["unrepaired"] == []
    assert fix["repair"]["report"]["operation_id"] == first.result["operation_id"]
    assert fix["changeset"]["stats"] == {
        "files": 1,
        "created": 0,
        "modified": 1,
        "deleted": 0,
        "lines_added": 1,
        "lines_removed": 3,
    }

    # 5. DAT validates again; only DAT says it passes.
    second = chain.run(
        "repo.quality",
        {"target": workspace, "fail_on": "low"},
        session_id=session,
        parent_operation_id=repaired.result["operation_id"],
    )
    assert ok(second)["gate"]["verdict"] == "pass"

    # 6. BLUX System reconstructs the whole chain from receipts alone.
    outcomes = (planned, built, first, repaired, second)
    ops = [o.result["operation_id"] for o in outcomes]
    code, graph = system("graph", "--store", str(store), "--session", session)
    assert code == 0 and graph["roots"] == [ops[0]] and graph["cycles"] == []
    assert sorted(map(tuple, graph["edges"])) == sorted(itertools.pairwise(ops))  # [parent, child]
    assert [graph["nodes"][o]["capability_id"] for o in ops] == [
        "reason.plan",
        "artifact.construct",
        "repo.quality",
        "artifact.repair",
        "repo.quality",
    ]
    assert all(graph["nodes"][o]["own_status"] == "SUCCESS" for o in ops)
    code, timeline = system("timeline", "--store", str(store), "--session", session)
    assert code == 0 and all(o in [e.get("operation_id") for e in timeline["timeline"]] for o in ops)
    code, verified = system("verify", "--store", str(store))
    assert code == 0 and verified["ok"] is True and verified["records"] == 5
    for op, outcome in zip(ops, outcomes, strict=True):
        code, receipt = system("show", "--store", str(store), "--operation", op)
        assert code == 0 and receipt["output_sha256"] == sha256_of(outcome.result["output"])

    # 7. Rollback artifacts are preserved: cA can undo the repair, and then the construction.
    proc = subprocess.run([CA, "status", "-w", workspace], capture_output=True, timeout=60)
    ledger = json.loads(proc.stdout)["ledger"]
    assert [(e["capability"], e["changeset_id"]) for e in ledger] == [
        ("artifact.construct", construction["changeset"]["changeset_id"]),
        ("artifact.repair", fix["changeset"]["changeset_id"]),
    ]
    proc = subprocess.run([CA, "rollback", fix["changeset"]["changeset_id"], "-w", workspace], capture_output=True)
    assert proc.returncode == 0 and Path(workspace, "app/core.py").read_text() == BUGGY


def test_repair_is_bounded_through_blux_and_the_refusal_is_recorded(store: Path, tmp_path: Path) -> None:
    chain = Chain(store)
    workspace = str(tmp_path / "build")
    ok(
        chain.run(
            "artifact.construct",
            {
                "workspace": workspace,
                "blueprint": {
                    "units": [
                        {"task": "build", "files": [{"path": "a.py", "content": "try:\n    pass\nexcept:\n    pass\n"}]}
                    ]
                },
            },
        )
    )
    attempts = []
    for _ in range(3):
        Path(workspace, "a.py").write_text("try:\n    pass\nexcept:\n    pass\n")  # the problem keeps coming back
        report = ok(chain.run("repo.quality", {"target": workspace, "fail_on": "low"}))
        assert report["gate"]["verdict"] == "fail"
        attempts.append(chain.run("artifact.repair", {"workspace": workspace, "report": report}))
    assert [ok(a)["repair"]["attempt"] for a in attempts[:2]] == [1, 2]
    refused = attempts[2].result
    assert refused["status"] == "failed" and refused["error"]["details"]["provider_code"] == "REPAIR_LIMIT_REACHED"
    code, receipt = system("show", "--store", str(store), "--operation", refused["operation_id"])
    assert code == 0 and receipt["error_code"] == "EXECUTION_FAILED"


def test_agents_may_check_but_not_write(store: Path, tmp_path: Path) -> None:
    chain = Chain(store)
    workspace = tmp_path / "w"
    workspace.mkdir()
    (workspace / "a.py").write_text("a = 1\n")
    denied = chain.run(
        "artifact.modify",
        {"workspace": str(workspace), "edits": [{"op": "write", "path": "a.py", "content": "pwned\n"}]},
        actor=AGENT,
    )
    assert (denied.result["status"], denied.result["error"]["code"]) == ("rejected", "POLICY_DENIED")
    assert (workspace / "a.py").read_text() == "a = 1\n" and not (workspace / ".ca").exists()
    planned = ok(
        chain.run(
            "artifact.modify",
            {
                "workspace": str(workspace),
                "dry_run": True,
                "edits": [{"op": "replace", "path": "a.py", "old": "1", "new": "2"}],
            },
        )
    )
    assert planned["status"] == "dry-run" and (workspace / "a.py").read_text() == "a = 1\n"
    check = ok(
        chain.run("artifact.validate", {"workspace": str(workspace), "changeset": planned["changeset"]}, actor=AGENT)
    )
    assert check["state"] == "applicable" and check["certified"] is False
    code, listing = system("list", "--store", str(store), "--status", "DENIED")
    assert code == 0 and listing["count"] == 1
