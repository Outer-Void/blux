"""Synthetic gate fixtures only; production training reads exclusively the sibling datasets."""

from __future__ import annotations

from pathlib import Path

import pytest
from training.router.baselines import CUES, baseline_predictions
from training.router.common import digest, read, write
from training.router.data import assert_fitting, load_split
from training.router.experiment import integration_gate
from training.router.fit import fit
from training.router.inference import advisory_or_fallback, load_model, predict, validate_artifact
from training.router.metrics import evaluate
from training.router.probe import project_record


@pytest.fixture
def model():
    return {
        "schema": "blux.router-artifact",
        "schema_version": 1,
        "model_id": "blux-router-0.1",
        "model_version": "0.1.0",
        "model_type": "hierarchical-tfidf-logistic",
        "feature_config": {"analyzer": "word", "ngram_range": [1, 2], "norm": "l2", "smooth_idf": True},
        "labels": ["artifact.modify", "reason.plan"],
        "unsupported_capabilities": [],
        "vocabulary": ["edit", "plan"],
        "idf": [1.0, 1.0],
        "family": {"labels": ["ca", "coga"], "weights": [[5, -5], [-5, 5]], "bias": [0, 0]},
        "heads": {
            "ca": {"labels": ["artifact.modify"], "weights": [[0, 0]], "bias": [0]},
            "coga": {"labels": ["reason.plan"], "weights": [[0, 0]], "bias": [0]},
        },
        "temperature": 1.0,
        "threshold": 0.9,
    }


def envelope(model):
    return {"payload": model, "sha256": digest(model)}


def row(text="plan work", split="train", label="reason.plan", family="f1"):
    return {"text": text, "split": split, "label": label, "family": family, "canary": None}


@pytest.mark.parametrize("split", ["test", "holdout", "adversarial", "regression"])
def test_protected_fit_guard(split):
    with pytest.raises(ValueError, match="protected"):
        assert_fitting([row(split=split)], ["canary"])


@pytest.mark.parametrize("split", ["test", "holdout", "adversarial", "regression"])
def test_protected_loader_guard(tmp_path, split):
    with pytest.raises(ValueError, match="protected"):
        load_split(tmp_path, tmp_path, split, fitting=True)


@pytest.mark.parametrize("record", [row(text="plan canary work"), {**row(), "canary": "canary"}])
def test_canary_guard(record):
    with pytest.raises(ValueError):
        assert_fitting([record], ["canary"])


def test_empty_fit_guard():
    with pytest.raises(ValueError):
        assert_fitting([], [])


@pytest.fixture
def source_record():
    config = {
        "dataset_id": "blux-ca-dataset",
        "engines": [{"name": "ca"}],
        "license": {"spdx": "test"},
        "canary": "CANARY",
    }
    r = {
        "record_id": "ca:f:01",
        "schema": "blux.dataset-record",
        "schema_version": 1,
        "dataset": "blux-ca-dataset",
        "dataset_version": "2.0.0",
        "family": "f",
        "split": "train",
        "task": {"instruction": "Edit the source safely"},
        "input": {"steps": [{"capability": "artifact.modify"}]},
        "provenance": {
            "authors": ["test"],
            "origin": "synthetic-authored",
            "review": {"status": "approved"},
            "engines": config["engines"],
            "license": config["license"],
            "generator": {"name": "test"},
            "seed": {"path": "test"},
        },
        "canary": None,
    }
    lock = {"records": {r["record_id"]: {"status": "active", "split": "train"}}}
    return r, config, lock


def test_label_extraction_and_no_output_features(source_record):
    r, config, lock = source_record
    r["expected"] = {"ignored": "must never become a feature"}
    projected = project_record(r, config, lock, {"artifact.modify"}, True)
    assert projected["label"] == "artifact.modify"
    assert projected["text"] == r["task"]["instruction"]
    assert "expected" not in projected


def test_ambiguous_label_excluded(source_record):
    r, config, lock = source_record
    r["input"]["steps"].append({"capability": "code.patch"})
    assert project_record(r, config, lock, {"artifact.modify", "code.patch"}, True)["label"] is None


@pytest.mark.parametrize("mutation", ["lock", "schema", "version", "provenance", "unknown", "canary"])
def test_record_gates(source_record, mutation):
    r, config, lock = source_record
    if mutation == "lock":
        lock["records"][r["record_id"]]["split"] = "test"
    if mutation == "schema":
        r["schema_version"] = 2
    if mutation == "version":
        r["dataset_version"] = "99"
    if mutation == "provenance":
        r["provenance"]["authors"] = []
    if mutation == "unknown":
        r["input"]["steps"][0]["capability"] = "made.up"
    if mutation == "canary":
        r["task"]["instruction"] += " CANARY"
    with pytest.raises(ValueError):
        project_record(r, config, lock, {"artifact.modify"}, True)


def test_roundtrip_and_deterministic_inference(model, tmp_path):
    path = tmp_path / "model.json"
    write(path, envelope(model))
    loaded = load_model(path, set(model["labels"]))
    assert loaded == model
    assert predict(loaded, "plan") == predict(model, "plan")
    assert predict(loaded, "plan")["capability"] == "reason.plan"
    assert predict(loaded, "edit")["capability"] == "artifact.modify"
    assert predict(loaded, "plan")["certified"] is False


@pytest.mark.parametrize("text", ["unknown words", "", "edit plan"])
def test_abstention(model, text):
    assert predict(model, text)["decision"] == "ABSTAIN"


def test_checksum_guard(model):
    doc = envelope(model)
    doc["payload"]["threshold"] = 0
    with pytest.raises(ValueError, match="checksum"):
        validate_artifact(doc, set(model["labels"]))


@pytest.mark.parametrize("mutation", ["schema_version", "unknown", "weights", "temperature", "idf", "feature", "nan"])
def test_artifact_rehashed_invalid(model, mutation):
    if mutation == "schema_version":
        model["schema_version"] = 2
    if mutation == "unknown":
        model["labels"].append("grant.authority")
    if mutation == "weights":
        model["family"]["weights"][0] = []
    if mutation == "temperature":
        model["temperature"] = -1
    if mutation == "idf":
        model["idf"][0] = -1
    if mutation == "feature":
        model["feature_config"]["norm"] = "l1"
    if mutation == "nan":
        model["family"]["weights"][0][0] = float("inf")
        with pytest.raises(ValueError):
            envelope(model)
        return
    with pytest.raises(ValueError):
        validate_artifact(envelope(model), {"reason.plan", "artifact.modify"})


@pytest.mark.parametrize("failure", ["missing", "corrupt", "unsupported", "unknown", "abstain", "malformed"])
def test_runtime_fallback(model, tmp_path, failure):
    path = tmp_path / "model.json"
    if failure == "unsupported":
        model["schema_version"] = 99
    if failure == "unknown":
        model["labels"].append("bypass.policy")
    if failure != "missing":
        write(path, envelope(model))
    if failure == "corrupt":
        path.write_text("{broken")
    if failure == "malformed":
        path.write_text("[]")
    calls = []

    def fallback():
        calls.append("existing-provider")
        return {"existing": True}

    result = advisory_or_fallback(
        path, "unknown" if failure == "abstain" else "plan", {"reason.plan", "artifact.modify"}, fallback
    )
    assert result == {"existing": True}
    assert calls == ["existing-provider"]


def test_baseline_and_metrics():
    rows = [row(), row(label="artifact.modify", text="edit files")]
    majority = baseline_predictions(rows, rows, sorted(CUES), "majority")
    keyword = baseline_predictions(rows, rows, sorted(CUES), "keyword")
    assert evaluate(rows, majority, sorted(CUES))["accuracy"] == 0.5
    assert evaluate(rows, keyword, sorted(CUES))["accuracy"] == 1
    assert evaluate(rows, keyword, sorted(CUES))["confusion_matrix"]["reason.plan"]["reason.plan"] == 1


def test_deterministic_fit_and_vocabulary_isolation():
    # Synthetic control fixture, not added to the production experiment's datasets.
    train = [
        row("edit file", label="artifact.modify", family="ca1"),
        row("modify file", label="artifact.modify", family="ca2"),
        row("plan work", label="reason.plan", family="cg1"),
        row("schedule work", label="reason.plan", family="cg2"),
    ]
    val = [
        row("edit validationonly", "validation", "artifact.modify", "ca3"),
        row("plan validationonly", "validation", "reason.plan", "cg3"),
    ]
    labels = ["artifact.modify", "reason.plan"]
    first, _ = fit(train, val, ["CANARY"], labels)
    second, _ = fit(train, val, ["CANARY"], labels)
    assert first == second
    assert "validationonly" not in first["vocabulary"]
    assert predict(first, "plan work") == predict(second, "plan work")
    val[0]["family"] = "ca1"
    with pytest.raises(ValueError, match="family"):
        fit(train, val, ["CANARY"], labels)


def test_gate_cannot_pass_unsupported_classes(model):
    metric = {
        "accuracy": 1,
        "macro_f1": 1,
        "coverage": 1,
        "family_routing_accuracy": 1,
        "accuracy_when_not_abstaining": 1,
    }
    report = {
        s: {"candidate": metric, "majority": {"accuracy": 0, "macro_f1": 0}, "keyword": {"accuracy": 0, "macro_f1": 0}}
        for s in ["test", "adversarial", "regression", "holdout"]
    }
    model["provenance"] = {"selection": {"threshold_target_met": True}}
    model["unsupported_capabilities"] = ["repo.patch"]
    assert not integration_gate(report, model)["passed"]


def test_policy_authority_is_unchanged(model, tmp_path):
    from blux_runtime.builtin import builtin_registry, reference_policy
    from blux_runtime.executor import Runtime
    from blux_sdk import envelopes

    path = tmp_path / "model.json"
    write(path, envelope(model))
    recommendation = advisory_or_fallback(path, "edit", set(model["labels"]), lambda: None)
    assert recommendation["authority"] == "advisory-only"
    runtime = Runtime(builtin_registry(), reference_policy())
    # An advisor cannot add a capability or policy grant to the real registry.
    request = envelopes.execution_request(recommendation["capability"], {}, actor={"type": "user", "id": "tester"})
    outcome = runtime.execute(request)
    assert outcome.result["status"] == "rejected"
    assert outcome.result["error"]["code"] == "UNKNOWN_CAPABILITY"


def test_duplicate_json_rejected(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{"a":1,"a":2}')
    with pytest.raises(ValueError, match="duplicate"):
        read(p)


@pytest.mark.parametrize("mutation", ["manifest_digest", "file_hash", "family", "provenance"])
def test_authoritative_integrity_gate(tmp_path, mutation):
    import os
    import shutil
    import subprocess

    source = Path("/workspace/blux-coga-dataset")
    interpreter = Path("/workspace/setup/blux-coga-dataset/bin/python")
    if not source.exists() or not interpreter.exists():
        pytest.skip("requires sibling dataset and its pinned environment")
    root = tmp_path / "dataset"
    shutil.copytree(
        source,
        root,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"),
    )
    manifest = read(root / "data/manifest.json")
    if mutation == "manifest_digest":
        manifest["records_sha256"] = "0" * 64
        write(root / "data/manifest.json", manifest)
    elif mutation == "file_hash":
        manifest["files"]["data/train.jsonl"]["sha256"] = "0" * 64
        write(root / "data/manifest.json", manifest)
    else:
        # Mutate only a training record; never inspect protected outputs in test design.
        import json

        path = root / "data/train.jsonl"
        records = [json.loads(line) for line in path.read_text().splitlines()]
        if mutation == "family":
            lock = read(root / "splits.lock.json")
            lock["records"][records[0]["record_id"]]["split"] = "holdout"
            write(root / "splits.lock.json", lock)
        else:
            records[0]["provenance"]["authors"] = []
            path.write_text("".join(json.dumps(r) + "\n" for r in records))
    caps_path = tmp_path / "caps.json"
    write(caps_path, {"coga": sorted(c for c in CUES if c.startswith("reason."))})
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    result = subprocess.run(
        [str(interpreter), "-m", "training.router.probe", str(root), "audit", str(caps_path)],
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode != 0, "a broken dataset was accepted"
    assert "ValueError" in result.stderr


def test_evaluation_cannot_repeat(tmp_path, monkeypatch, model):
    from training.router import experiment

    monkeypatch.setattr(experiment, "check_frozen", lambda *a: ({}, model, model["labels"]))
    write(tmp_path / "reproduction.json", {"parameters_identical": True})
    (tmp_path / "evaluation.started").write_text("already attempted")
    with pytest.raises(FileExistsError):
        experiment.protected_evaluate(tmp_path, tmp_path)


def test_recommendation_cannot_grant_permissions(model, tmp_path):
    import copy

    from blux_runtime.builtin import echo, reference_policy
    from blux_runtime.executor import Runtime
    from blux_runtime.registry import CapabilityRegistry
    from blux_sdk import envelopes

    path = tmp_path / "model.json"
    write(path, envelope(model))
    recommendation = advisory_or_fallback(path, "edit", set(model["labels"]), lambda: None)
    descriptor = copy.deepcopy(echo.DESCRIPTOR)
    descriptor.update(
        capability_id=recommendation["capability"], side_effects="write", required_permissions=["fs:write"]
    )
    registry = CapabilityRegistry()
    calls = []
    registry.register(descriptor, lambda *args: calls.append("executed"))
    policy = {
        **reference_policy(),
        "rules": [
            {
                "rule_id": "unprivileged",
                "effect": "allow",
                "capabilities": [recommendation["capability"]],
                "max_side_effects": "write",
                "grants": [],
            }
        ],
    }
    request = envelopes.execution_request(
        recommendation["capability"], {"message": "test"}, actor={"type": "user", "id": "test"}
    )
    outcome = Runtime(registry, policy).execute(request)
    assert outcome.result["error"]["code"] == "POLICY_DENIED"
    assert calls == []


def test_missing_dataset_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="absent"):
        load_split(tmp_path, tmp_path, "train", fitting=True)


def test_experiment_receipt_tampering_detected(tmp_path):
    import shutil

    from training.verify_experiment import verify

    source = Path(__file__).resolve().parents[1] / "experiments/blux-router-0.1"
    if not source.exists():
        pytest.skip("completed experiment evidence not available")
    shutil.copytree(source, tmp_path / "experiment")
    root = tmp_path / "experiment"
    assert verify(root)["gate_passed"] is False
    metrics = read(root / "test-metrics.json")
    metrics["candidate"]["accuracy"] = 1
    write(root / "test-metrics.json", metrics)
    with pytest.raises(ValueError, match="metrics receipt"):
        verify(root)
