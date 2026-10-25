"""HARD GATE (Stage 05): BLUX -> GOD discovers LRC -> LRC creates the fixture -> DAT audits it -> BLUX System
reconstructs the complete chain.

BLUX does not know where LRC lives: it asks GOD (capability.discover), loads the project GOD names, and runs
the chain as linked operations. Needs `god`, `lrc`, `dat` and `blux-system` and checkouts of GOD, LRC and
DAT (CI: GOD_PROJECT_DIR, LRC_PROJECT_DIR, DAT_PROJECT_DIR). Skips if anything is missing, unless
BLUX_REQUIRE_GOD=1 (CI).
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
from blux_runtime.registry import CapabilityRegistry
from blux_runtime.system_sink import BluxSystemSink
from blux_sdk import dot_blux, envelopes, ids
from blux_sdk.serialization import sha256_of
from tests.conftest import ROOT


def _exe(name: str) -> str | None:
    local = Path(sys.executable).with_name(name)
    return str(local) if local.exists() else shutil.which(name)


GOD, LRC, DAT, SYSTEM = _exe("god"), _exe("lrc"), _exe("dat"), _exe("blux-system")
GOD_PROJECT = Path(os.environ.get("GOD_PROJECT_DIR", ROOT.parent / "god"))
LRC_PROJECT = Path(os.environ.get("LRC_PROJECT_DIR", ROOT.parent / "lrc"))
DAT_PROJECT = Path(os.environ.get("DAT_PROJECT_DIR", ROOT.parent / "dat"))
FIXTURE = LRC_PROJECT / "tests" / "fixtures" / "hardgate.lrc"
missing = [
    n
    for n, ok in (
        ("god", GOD),
        ("lrc", LRC),
        ("dat", DAT),
        ("blux-system", SYSTEM),
        ("git", shutil.which("git")),
        ("GOD .blux", (GOD_PROJECT / ".blux").is_dir()),
        ("LRC fixture", FIXTURE.is_file()),
        ("DAT .blux", (DAT_PROJECT / ".blux").is_dir()),
    )
    if not ok
]
if missing:
    if os.environ.get("BLUX_REQUIRE_GOD") == "1":
        raise RuntimeError(f"BLUX_REQUIRE_GOD=1 but missing: {missing}")
    pytest.skip(f"missing: {missing}", allow_module_level=True)
assert SYSTEM is not None
EXPECTED = json.loads((FIXTURE.parent / "hardgate.expected.json").read_text())
SERVICE = {"type": "service", "id": "ci"}


@pytest.fixture(autouse=True)
def _tools_on_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dirs = dict.fromkeys(str(Path(p).parent) for p in (GOD, LRC, DAT, SYSTEM) if p)
    monkeypatch.setenv("PATH", os.pathsep.join([*dirs, os.environ["PATH"]]))
    monkeypatch.setenv("GOD_INDEX", str(tmp_path / "god-index.json"))


def system(*args: str) -> tuple[int, Any]:
    proc = subprocess.run([SYSTEM, *args], capture_output=True, timeout=60)
    return proc.returncode, json.loads(proc.stdout)


@pytest.fixture
def store(tmp_path: Path) -> Path:
    path = tmp_path / "truth"
    assert system("init", "--store", str(path))[0] == 0
    return path


class Chain:
    """A BLUX runtime that starts knowing only GOD and learns other projects from GOD's answers."""

    def __init__(self, store: Path) -> None:
        self.registry: CapabilityRegistry = builtin_registry()
        self.rules: list[dict[str, Any]] = []
        self.sink = BluxSystemSink(store, executable=SYSTEM)
        self.load(GOD_PROJECT)

    def load(self, root: Path) -> None:
        project = dot_blux.load(root)
        register_project(self.registry, project)
        self.rules.extend(project.policy["rules"])

    def run(self, capability: str, payload: dict[str, Any], **kw: Any) -> Any:
        policy = {
            "contract": "blux.policy",
            "contract_version": "1.0",
            "policy_id": "stage-05-chain",
            "default_effect": "deny",
            "rules": self.rules,
        }
        rt = Runtime(self.registry, policy, receipt_sink=self.sink)
        outcome = rt.execute(envelopes.execution_request(capability, payload, actor=SERVICE, **kw))
        assert outcome.sink_error is None
        return outcome


def test_hard_gate_god_discovers_lrc_creates_dat_audits_system_reconstructs(store: Path, tmp_path: Path) -> None:
    chain = Chain(store)
    session = ids.new_session_id()
    roots = [str(LRC_PROJECT), str(DAT_PROJECT)]

    # 1. GOD discovers who provides repo.create. BLUX has not loaded LRC yet.
    with pytest.raises(Exception, match=r"repo\.create"):
        chain.registry.get("repo.create")
    found = chain.run("capability.discover", {"roots": roots, "query": "repo.create"}, session_id=session)
    assert found.result["status"] == "succeeded", found.result["error"]
    disc = found.result["output"]
    assert disc["contract"] == "god.capabilities" and disc["problems"] == [] and disc["duplicates"] == []
    (provider,) = disc["capabilities"]
    assert (provider["capability_id"], provider["project_id"]) == ("repo.create", "lrc")
    assert provider["provider"]["entrypoint"] == "lrc blux-capability repo.create"
    assert disc["linkage"]["operation_id"] == found.result["operation_id"]
    assert found.receipt["output_sha256"] == sha256_of(disc)

    # 2. BLUX loads the project GOD named, then LRC creates the reproducible fixture.
    chain.load(Path(provider["project_root"]))
    chain.load(DAT_PROJECT)
    created = chain.run(
        "repo.create",
        {"spec_file": str(FIXTURE), "target": str(tmp_path / "fixture")},
        session_id=session,
        parent_operation_id=found.result["operation_id"],
    )
    assert created.result["status"] == "succeeded", created.result["error"]
    out = created.result["output"]
    assert (out["plan_id"], out["git"]["commit"]) == (EXPECTED["plan_id"], EXPECTED["git_commit"])

    # 3. DAT audits it, as LRC's handoff says.
    handoff = out["handoff"]
    audited = chain.run(
        handoff["capability_id"],
        handoff["input"],
        session_id=session,
        parent_operation_id=created.result["operation_id"],
    )
    assert audited.result["status"] == "succeeded", audited.result["error"]
    assert audited.result["output"]["gate"]["verdict"] == "pass"

    # 4. BLUX System reconstructs the complete chain from receipts alone.
    ops = [o.result["operation_id"] for o in (found, created, audited)]
    code, graph = system("graph", "--store", str(store), "--session", session)
    assert code == 0 and graph["roots"] == [ops[0]] and graph["cycles"] == []
    assert set(graph["nodes"]) == set(ops)
    assert sorted(map(tuple, graph["edges"])) == sorted([(ops[0], ops[1]), (ops[1], ops[2])])  # [parent, child]
    caps = [graph["nodes"][o]["capability_id"] for o in ops]
    assert caps == ["capability.discover", "repo.create", "repo.audit"]
    assert all(graph["nodes"][o]["own_status"] == "SUCCESS" for o in ops)
    assert graph["nodes"][ops[0]]["aggregate_status"] == "SUCCESS"
    code, timeline = system("timeline", "--store", str(store), "--session", session)
    assert code == 0 and timeline["count"] >= 3
    seen = [e.get("operation_id") for e in timeline["timeline"]]
    assert all(o in seen for o in ops)
    code, verified = system("verify", "--store", str(store))
    assert code == 0 and verified["ok"] is True and verified["records"] == 3
    for o, outcome in zip(ops, (found, created, audited), strict=True):
        code, receipt = system("show", "--store", str(store), "--operation", o)
        assert code == 0 and receipt["output_sha256"] == sha256_of(outcome.result["output"])


def test_god_findings_are_results_and_lookup_failures_are_recorded(store: Path, tmp_path: Path) -> None:
    broken = tmp_path / "roots" / "broken" / ".blux"
    broken.mkdir(parents=True)
    (broken / "manifest.yaml").write_text("contract: blux.project\nproject_id: [oops\n")
    shutil.copytree(LRC_PROJECT / ".blux", tmp_path / "roots" / "lrc-copy" / ".blux")
    chain = Chain(store)
    roots = [str(LRC_PROJECT), str(tmp_path / "roots")]
    found = chain.run("capability.discover", {"roots": roots, "query": "repo.*"})
    assert found.result["status"] == "succeeded"
    disc = found.result["output"]
    assert [p["issue"] for p in disc["problems"]] == ["malformed-yaml"]
    assert [d["issue"] for d in disc["duplicates"]] == ["duplicate-project"]
    ambiguous = chain.run("capability.describe", {"roots": roots, "capability_id": "repo.create"})
    assert ambiguous.result["status"] == "failed"
    assert ambiguous.result["error"]["details"]["provider_code"] == "AMBIGUOUS"
    code, graph = system("graph", "--store", str(store))
    assert code == 0
    assert graph["nodes"][found.result["operation_id"]]["own_status"] == "SUCCESS"
    assert graph["nodes"][ambiguous.result["operation_id"]]["own_status"] == "FAILURE"


def test_god_is_read_only_through_blux(store: Path, tmp_path: Path) -> None:
    project = dot_blux.load(GOD_PROJECT)
    effects = {c.capability_id: c.side_effects for c in project.capabilities()}
    assert {cid for cid, e in effects.items() if e != "read"} == {"registry.index"}
    marker = tmp_path / "ran"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    canary = bindir / "lrc"
    canary.write_text(f"#!{sys.executable}\nopen({str(marker)!r}, 'w').write('ran')\n")
    canary.chmod(0o755)
    chain = Chain(store)
    found = chain.run("command.discover", {"roots": [str(LRC_PROJECT)], "path": str(bindir), "declared": True})
    assert found.result["status"] == "succeeded"
    assert {d["status"] for d in found.result["output"]["declared"]} == {"found"}
    assert not marker.exists()  # GOD found the program and did not run it
    assert not (tmp_path / "god-index.json").exists()  # read capabilities never write the cache
