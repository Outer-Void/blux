"""Stage 34 hard gate: a synthetic or approved trajectory passes the whole pipeline; secret-bearing, private,
hidden-reasoning and ineligible trajectories are rejected or sanitized according to policy."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from blux_sdk import schemas as contract_schemas
from blux_training import pipeline
from blux_training.__main__ import main

RECEIPT: dict[str, Any] = {
    "actor": {"id": "fixture", "type": "user"},
    "artifact_ids": ["art_01JAAAAAAAAAAAAAAAAAAAAAAA"],
    "capability_id": "reason.plan",
    "capability_version": "1.0.0",
    "contract": "blux.receipt",
    "contract_version": "1.0",
    "error_code": None,
    "finished_at": "2026-10-24T00:00:00.250Z",
    "input_sha256": "e" * 64,
    "issuer": {"component": "blux-runtime", "version": "0.4.0"},
    "operation_id": "op_01JAAAAAAAAAAAAAAAAAAAAAAA",
    "output_sha256": "f" * 64,
    "parent_operation_id": None,
    "policy_decision": {"effect": "allow", "policy_id": "blux.reference", "reason": "ok", "rule_id": "allow-plan"},
    "project_id": "blux",
    "receipt_id": "rcpt_01JAAAAAAAAAAAAAAAAAAAAAAA",
    "session_id": "ses_01JAAAAAAAAAAAAAAAAAAAAAAA",
    "started_at": "2026-10-24T00:00:00.000Z",
    "status": "succeeded",
}


def synthetic(task: str = "Order the release steps: build, sign, publish") -> dict[str, Any]:
    return {
        "kind": "blux.trajectory-source",
        "version": "1",
        "source_type": "synthetic",
        "source_project": "blux",
        "eligibility": {"basis": "synthetic", "data_class": "synthetic"},
        "receipts": [],
        "trajectory": {
            "input": {"task": task},
            "intent": "reason.plan",
            "context_summary": "Three dependent release steps.",
            "reasoning_structure": {
                "steps": ["build", "sign", "publish"],
                "edges": [["build", "sign"], ["sign", "publish"]],
            },
            "planned_actions": [{"capability": "reason.plan"}],
            "tool_calls": [{"capability": "reason.plan", "input_sha256": "e" * 64}],
            "tool_results": [{"status": "succeeded"}],
            "constructed_artifacts": [{"artifact_id": "art_plan", "license": "LicenseRef-OuterVoid-Proprietary"}],
            "validation_results": [{"check": "dag-acyclic", "passed": True}],
            "repair_iterations": 0,
            "final_result": {"order": ["build", "sign", "publish"]},
            "accept_reject": "accept",
        },
        "licenses": [{"subject": "record", "spdx": "LicenseRef-OuterVoid-Proprietary"}],
        "model_versions": {},
        "engine_versions": {"coga": "2.0.0"},
    }


def approved_operation() -> dict[str, Any]:
    src = synthetic("Plan the device diagnosis for a boot loop")
    src.update(source_type="blux-operation", receipts=[copy.deepcopy(RECEIPT)])
    src["eligibility"] = {
        "basis": "owner-approved",
        "data_class": "public",
        "approved_by": "outer-void owner",
        "approved_at": "2026-10-27",
    }
    return src


def write(tmp: Path, name: str, doc: Any) -> None:
    (tmp / "in").mkdir(exist_ok=True)
    (tmp / "in" / f"{name}.json").write_text(json.dumps(doc) if not isinstance(doc, str) else doc)


def build(tmp: Path, **kw: Any) -> dict[str, Any]:
    return pipeline.build(tmp / "in", tmp / "out", version="test-1", **kw)


def outcome(result: dict[str, Any], name: str) -> dict[str, Any]:
    return next(o for o in result["report"]["outcomes"] if o["source"] == f"{name}.json")


def records(tmp: Path) -> list[dict[str, Any]]:
    out = []
    for split in pipeline.SPLITS:
        out += [json.loads(line) for line in (tmp / "out" / "splits" / f"{split}.jsonl").read_text().splitlines()]
    return out


def test_receipt_fixture_is_a_valid_contract_receipt() -> None:
    assert contract_schemas.schema_errors("receipt", RECEIPT) == []


def test_synthetic_and_approved_trajectories_pass_end_to_end(tmp_path: Path) -> None:
    write(tmp_path, "synthetic", synthetic())
    write(tmp_path, "approved", approved_operation())
    result = build(tmp_path)
    assert outcome(result, "synthetic")["status"] == "accepted"
    assert outcome(result, "approved")["status"] == "accepted"
    recs = {r["source_type"]: r for r in records(tmp_path)}
    op = recs["blux-operation"]
    assert op["operation_id"] == RECEIPT["operation_id"] and op["provenance"]["receipt_ids"] == [RECEIPT["receipt_id"]]
    assert op["privacy_class"] == "public" and recs["synthetic"]["privacy_class"] == "synthetic"
    for r in recs.values():
        assert set(r) >= {
            "record_id",
            "schema_version",
            "quality_scores",
            "provenance",
            "hashes",
            "dataset_split",
            "engine_versions",
        }
        assert pipeline.schema_errors("record", r) == []
    assert pipeline.validate(tmp_path / "out") == []


@pytest.mark.parametrize("where", ["input", "final_result", "tool_results"])
def test_secret_bearing_trajectory_is_rejected_and_never_written(tmp_path: Path, where: str) -> None:
    src = synthetic()
    token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    if where == "input":
        src["trajectory"]["input"] = {"task": f"deploy with {token}"}
    elif where == "final_result":
        src["trajectory"]["final_result"] = {"config": "api_key = sk_live_abcdef0123456789"}
    else:
        src["trajectory"]["tool_results"] = [{"stdout": "password: hunter22"}]
    write(tmp_path, "secret", src)
    result = build(tmp_path)
    o = outcome(result, "secret")
    assert o["status"] == "rejected" and any(r.startswith("secret:") for r in o["reasons"])
    dumped = "".join((tmp_path / "out" / f).read_text() for f in ("report.json", "manifest.json"))
    dumped += "".join(p.read_text() for p in (tmp_path / "out" / "splits").iterdir())
    assert token not in dumped and "hunter22" not in dumped and "sk_live_abcdef" not in dumped


def test_private_data_is_never_training_data_even_if_approved(tmp_path: Path) -> None:
    src = approved_operation()
    src["eligibility"]["data_class"] = "private"
    write(tmp_path, "private", src)
    o = outcome(build(tmp_path), "private")
    assert o["status"] == "rejected" and "private user/project data" in o["reasons"][0]


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda s: s["eligibility"].update(basis="owner-approved"), "synthetic source must declare"),
        (
            lambda s: (
                s.update(source_type="blux-operation"),
                s["eligibility"].update(basis="owner-approved", data_class="public"),
            ),
            "owner approval",
        ),
        (
            lambda s: (
                s.update(source_type="blux-operation"),
                s["eligibility"].update(
                    basis="owner-approved", data_class="public", approved_by="o", approved_at="2026-10-27"
                ),
            ),
            "needs its receipts",
        ),
    ],
)
def test_ineligible_sources_are_rejected(tmp_path: Path, mutate: Any, reason: str) -> None:
    src = synthetic()
    mutate(src)
    write(tmp_path, "inel", src)
    o = outcome(build(tmp_path), "inel")
    assert o["status"] == "rejected" and any(reason in r for r in o["reasons"])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t["reasoning_structure"].update(chain_of_thought="first I think..."),
        lambda t: t.update(final_result={"scratchpad": "x", "order": []}),
        lambda t: t.update(context_summary="<thinking>hidden</thinking> summary"),
    ],
)
def test_provider_hidden_reasoning_is_never_stored(tmp_path: Path, mutate: Any) -> None:
    src = synthetic()
    mutate(src["trajectory"])
    write(tmp_path, "cot", src)
    o = outcome(build(tmp_path), "cot")
    assert o["status"] == "rejected" and any(r.startswith("hidden-reasoning:") for r in o["reasons"])


def test_personal_data_is_redacted_and_marked(tmp_path: Path) -> None:
    src = synthetic(
        "Email jane.doe@realmail.io from 203.0.113.9 and 8.8.4.4 about /home/alexr/notes; "
        "call +1 555 867 5309; Acme Secret Labs"
    )
    write(tmp_path, "pii", src)
    result = build(tmp_path, private_terms=("Acme Secret Labs",))
    o = outcome(result, "pii")
    assert o["status"] == "sanitized"
    (rec,) = records(tmp_path)
    text = rec["input"]["task"]
    assert (
        "jane.doe" not in text
        and "8.8.4.4" not in text
        and "alexr" not in text
        and "867 5309" not in text
        and "Acme" not in text
    )
    assert "203.0.113.9" in text  # documentation range is kept
    assert rec["privacy_class"] == "redacted" and set(rec["provenance"]["redactions"]) == {
        "email",
        "ipv4",
        "home-path",
        "phone",
        "private-term",
    }
    assert pipeline.validate(tmp_path / "out") == []


def test_unlicensed_artifacts_are_rejected(tmp_path: Path) -> None:
    src = synthetic()
    src["trajectory"]["constructed_artifacts"] = [{"artifact_id": "art_x", "license": "GPL-3.0-only"}]
    write(tmp_path, "lic", src)
    o = outcome(build(tmp_path), "lic")
    assert o["status"] == "rejected" and "license" in o["reasons"][0]


def test_invalid_receipts_and_malformed_sources_are_rejected(tmp_path: Path) -> None:
    bad = approved_operation()
    bad["receipts"][0]["input_sha256"] = "XYZ"
    write(tmp_path, "badreceipt", bad)
    write(tmp_path, "notjson", "{nope")
    write(tmp_path, "wrongshape", {"kind": "something-else"})
    result = build(tmp_path)
    for name in ("badreceipt", "notjson", "wrongshape"):
        assert outcome(result, name)["status"] == "rejected", name


def test_exact_and_near_duplicates_are_removed(tmp_path: Path) -> None:
    write(tmp_path, "a", synthetic())
    write(tmp_path, "b", synthetic())
    near = synthetic("Order the release steps: build, sign, publish!")
    write(tmp_path, "c", near)
    result = build(tmp_path)
    assert outcome(result, "a")["status"] == "accepted"
    assert outcome(result, "b")["reasons"][0].startswith("duplicate: same content")
    assert outcome(result, "c")["reasons"][0].startswith("duplicate:")


def test_contamination_with_protected_evaluation_sets_is_excluded(tmp_path: Path) -> None:
    holdout = tmp_path / "holdout.jsonl"
    holdout.write_text(
        json.dumps(
            {
                "task": "Order the release steps: build, sign, publish",
                "intent": "reason.plan",
                "order": ["build", "sign", "publish"],
            }
        )
        + "\n"
    )
    write(tmp_path, "leak", synthetic())
    o = outcome(build(tmp_path, protected=[holdout]), "leak")
    assert o["status"] == "rejected" and o["reasons"][0].startswith("contamination:")


def test_low_quality_is_rejected(tmp_path: Path) -> None:
    src = synthetic()
    t = src["trajectory"]
    for k in ("context_summary", "reasoning_structure", "planned_actions", "tool_calls", "tool_results"):
        t.pop(k)
    t["validation_results"] = [{"check": "x", "passed": False}]
    t["repair_iterations"] = 5
    write(tmp_path, "weak", src)
    assert outcome(build(tmp_path), "weak")["reasons"][0].startswith("quality:")


def test_split_hints_protect_and_the_lock_never_moves_records(tmp_path: Path) -> None:
    held = synthetic("Rank three mitigations for a flaky CI runner")
    held["split_hint"] = "holdout"
    write(tmp_path, "held", held)
    for i, task in enumerate(
        ["Decompose the launcher settings screen", "Plan the backup restore drill", "Compare two queue designs"]
    ):
        write(tmp_path, f"t{i}", synthetic(task))
    first = build(tmp_path)
    assert outcome(first, "held")["split"] == "holdout"
    lock = tmp_path / "out" / "split-lock.json"
    assignments = json.loads(lock.read_text())["assignments"]
    # tamper: a later run must keep every locked record where it was, even if the hint disappears
    (tmp_path / "in" / "held.json").write_text(json.dumps({**held, "split_hint": None} | {"split_hint": "test"}))
    second = pipeline.build(tmp_path / "in", tmp_path / "out2", version="test-2", lock_path=lock)
    for o in second["report"]["outcomes"]:
        if o.get("record_id") in assignments:
            assert o["split"] == assignments[o["record_id"]]


def test_builds_are_deterministic(tmp_path: Path) -> None:
    write(tmp_path, "s", synthetic())
    write(tmp_path, "a", approved_operation())
    pipeline.build(tmp_path / "in", tmp_path / "o1", version="v")
    pipeline.build(tmp_path / "in", tmp_path / "o2", version="v")
    for rel in ("manifest.json", "report.json", "split-lock.json", *(f"splits/{s}.jsonl" for s in pipeline.SPLITS)):
        assert (tmp_path / "o1" / rel).read_bytes() == (tmp_path / "o2" / rel).read_bytes(), rel


def test_validate_detects_tampering(tmp_path: Path) -> None:
    write(tmp_path, "s", synthetic())
    build(tmp_path)
    split = next(p for p in (tmp_path / "out" / "splits").iterdir() if p.read_text())
    rec = json.loads(split.read_text().splitlines()[0])
    rec["final_result"] = {"order": ["publish"], "note": "token: abcdef123456"}
    split.write_text(json.dumps(rec) + "\n")
    problems = pipeline.validate(tmp_path / "out")
    assert (
        any("digest" in p for p in problems)
        and any("record_sha256" in p for p in problems)
        and any("secret" in p for p in problems)
    )


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path, "s", synthetic())
    assert main(["build", str(tmp_path / "in"), "--out", str(tmp_path / "out"), "--version", "cli-1"]) == 0
    assert '"accepted": 1' in capsys.readouterr().out
    assert main(["validate", str(tmp_path / "out")]) == 0
    assert main(["build", str(tmp_path / "missing"), "--out", str(tmp_path / "x"), "--version", "1"]) == 2
