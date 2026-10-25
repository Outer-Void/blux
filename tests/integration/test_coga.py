"""HARD GATE (Stage 06): BLUX invokes CogA; results validate against the schema and can be replay-linked
through BLUX System.

Needs the `coga` and `blux-system` executables and a blux-coga checkout (CI: COGA_PROJECT_DIR). Skips if
anything is missing, unless BLUX_REQUIRE_COGA=1 (CI).
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
from blux_sdk.capability import CapabilityDescriptor
from blux_sdk.serialization import sha256_of
from tests.conftest import ROOT


def _exe(name: str) -> str | None:
    local = Path(sys.executable).with_name(name)
    return str(local) if local.exists() else shutil.which(name)


COGA, SYSTEM = _exe("coga"), _exe("blux-system")
COGA_PROJECT = Path(os.environ.get("COGA_PROJECT_DIR", ROOT.parent / "blux-coga"))
EVALS = COGA_PROJECT / "src" / "blux_coga" / "reason" / "evals"
missing = [
    n
    for n, ok in (
        ("coga", COGA),
        ("blux-system", SYSTEM),
        ("CogA .blux", (COGA_PROJECT / ".blux").is_dir()),
        ("CogA evals", (EVALS / "suite.json").is_file()),
    )
    if not ok
]
if missing:
    if os.environ.get("BLUX_REQUIRE_COGA") == "1":
        raise RuntimeError(f"BLUX_REQUIRE_COGA=1 but missing: {missing}")
    pytest.skip(f"missing: {missing}", allow_module_level=True)
assert COGA is not None and SYSTEM is not None
SUITE = json.loads((EVALS / "suite.json").read_text())
REGRESSION = [c for c in SUITE["cases"] if c["kind"] == "regression"]
AGENT = {"type": "agent", "id": "planner"}


@pytest.fixture(autouse=True)
def _tools_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    dirs = dict.fromkeys(str(Path(p).parent) for p in (COGA, SYSTEM))
    monkeypatch.setenv("PATH", os.pathsep.join([*dirs, os.environ["PATH"]]))


def system(*args: str) -> tuple[int, Any]:
    proc = subprocess.run([SYSTEM, *args], capture_output=True, timeout=60)
    return proc.returncode, json.loads(proc.stdout)


@pytest.fixture
def store(tmp_path: Path) -> Path:
    path = tmp_path / "truth"
    assert system("init", "--store", str(path))[0] == 0
    return path


def runtime(store: Path) -> tuple[Runtime, dict[str, CapabilityDescriptor]]:
    project = dot_blux.load(COGA_PROJECT)
    registry = builtin_registry()
    register_project(registry, project)
    caps = {c.capability_id: c for c in project.capabilities()}
    return Runtime(registry, project.policy, receipt_sink=BluxSystemSink(store, executable=SYSTEM)), caps


def request(case: dict[str, Any]) -> dict[str, Any]:
    return json.loads((EVALS / case["request"]).read_text())  # type: ignore[no-any-return]


def run(rt: Runtime, capability: str, payload: dict[str, Any], **kw: Any) -> Any:
    outcome = rt.execute(envelopes.execution_request(capability, payload, actor=AGENT, **kw))
    assert outcome.sink_error is None
    return outcome


def test_hard_gate_blux_invokes_coga_schema_valid_and_replay_linked(store: Path, tmp_path: Path) -> None:
    rt, caps = runtime(store)
    session = ids.new_session_id()
    assert {c["capability"] for c in REGRESSION} == set(caps)  # every capability, through BLUX
    for case in REGRESSION:
        cap, payload = case["capability"], request(case)
        outcome = run(rt, cap, payload, session_id=session)
        assert outcome.result["status"] == "succeeded", (cap, outcome.result["error"])
        artifact = outcome.result["output"]

        # 1. Schema-valid: BLUX checked it against output_schema; check again explicitly with the SDK.
        caps[cap].validate_output(artifact)
        assert artifact["contract"] == "coga.artifact" and artifact["capability"] == cap
        # Same result BLUX-side as standalone: the artifact id the eval suite pins.
        assert artifact["artifact_id"] == case["expect"]["artifact_id"]
        assert artifact["linkage"]["operation_id"] == outcome.result["operation_id"]

        # 2. Recorded: the receipt hashes this request and this exact artifact.
        op = outcome.result["operation_id"]
        code, receipt = system("show", "--store", str(store), "--operation", op)
        assert code == 0 and receipt["output_sha256"] == sha256_of(artifact)
        assert receipt["input_sha256"] == sha256_of(payload)

        # 3. Replay-linked: standalone CogA recomputes the stored artifact and BLUX System confirms both hashes.
        art_path, req_path, re_path = (
            tmp_path / f"{op}.art.json",
            tmp_path / f"{op}.req.json",
            tmp_path / f"{op}.re.json",
        )
        art_path.write_text(json.dumps(artifact))
        req_path.write_text(json.dumps(payload))
        proc = subprocess.run(
            [COGA, "replay", str(art_path), "--request", str(req_path), "-o", str(re_path)],
            capture_output=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stdout
        assert json.loads(proc.stdout)["ok"] is True
        code, check = system(
            "replay-check", op, "--store", str(store), "--input", str(req_path), "--output", str(re_path)
        )
        assert code == 0 and check == {"operation_id": op, "input_match": True, "output_match": True, "ok": True}

    code, meta = system("list", "--store", str(store), "--status", "SUCCESS")
    assert code == 0 and meta["count"] == len(REGRESSION)
    code, verified = system("verify", "--store", str(store))
    assert code == 0 and verified["ok"] is True


def test_a_tampered_result_does_not_replay(store: Path, tmp_path: Path) -> None:
    rt, _ = runtime(store)
    case = next(c for c in REGRESSION if c["capability"] == "reason.compare")
    outcome = run(rt, case["capability"], request(case))
    tampered = json.loads(json.dumps(outcome.result["output"]))
    tampered["result"]["leaders"] = ["mongo"]
    (tmp_path / "t.json").write_text(json.dumps(tampered))
    (tmp_path / "r.json").write_text(json.dumps(request(case)))
    proc = subprocess.run(
        [
            COGA,
            "replay",
            str(tmp_path / "t.json"),
            "--request",
            str(tmp_path / "r.json"),
            "-o",
            str(tmp_path / "re.json"),
        ],
        capture_output=True,
    )
    assert proc.returncode == 1
    op = outcome.result["operation_id"]
    code, check = system("replay-check", op, "--store", str(store), "--output", str(tmp_path / "t.json"))
    assert code != 0 or check["ok"] is False
    assert check["ok"] is False and check["output_match"] is False


def test_same_request_in_two_operations_links_by_artifact_id(store: Path) -> None:
    rt, _ = runtime(store)
    case = next(c for c in REGRESSION if c["capability"] == "reason.plan")
    first = run(rt, case["capability"], request(case))
    second = run(rt, case["capability"], request(case), parent_operation_id=first.result["operation_id"])
    a, b = first.result["output"], second.result["output"]
    assert a["artifact_id"] == b["artifact_id"] and a["linkage"] != b["linkage"]
    assert first.receipt["input_sha256"] == second.receipt["input_sha256"]
    assert first.receipt["output_sha256"] != second.receipt["output_sha256"]  # outputs differ only in linkage
    code, graph = system("graph", "--store", str(store))
    assert code == 0
    assert graph["nodes"][second.result["operation_id"]]["parent_operation_id"] == first.result["operation_id"]


def test_rejections_are_recorded_and_distinguishable(store: Path) -> None:
    rt, _ = runtime(store)
    by_schema = run(
        rt, "reason.rank", {"contract": "coga.request", "version": 1, "items": [{"id": "x", "hidden_reasoning": "?"}]}
    )
    # BLUX rejects a schema-invalid request before CogA runs; CogA never sees it.
    assert by_schema.result["status"] == "rejected" and by_schema.result["error"]["code"] == "SCHEMA_VALIDATION_FAILED"
    cyclic = {
        "contract": "coga.request",
        "version": 1,
        "tasks": [{"id": "a", "title": "A", "depends_on": ["b"]}, {"id": "b", "title": "B", "depends_on": ["a"]}],
    }
    by_engine = run(rt, "reason.plan", cyclic)  # schema-valid, but CogA cannot order a cycle
    assert by_engine.result["error"]["code"] == "EXECUTION_FAILED"
    assert by_engine.result["error"]["details"]["provider_code"] == "DEPENDENCY_CYCLE"
    assert by_engine.result["error"]["details"]["provider_details"]["cycle"] == ["a", "b", "a"]
    # BLUX System counts both as FAILURE (only policy denials are DENIED); the receipts keep them apart.
    receipts = {}
    for o in (by_schema, by_engine):
        code, r = system("show", "--store", str(store), "--operation", o.result["operation_id"])
        assert code == 0
        receipts[o.result["operation_id"]] = (r["status"], r["error_code"])
    assert sorted(receipts.values()) == [("failed", "EXECUTION_FAILED"), ("rejected", "SCHEMA_VALIDATION_FAILED")]
    code, listing = system("list", "--store", str(store), "--status", "FAILURE")
    assert code == 0 and listing["count"] == 2
