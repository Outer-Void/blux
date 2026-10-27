"""Synthetic control fixtures are rejected as production data; they never augment the real experiment."""

from __future__ import annotations

import jsonschema
import pytest
from training.phase2.artifact import advisory_or_fallback, validate
from training.phase2.baselines import predictions
from training.phase2.dataset import build, check_row, coverage, fit_rows, load_verified, signature, verify_rows
from training.phase2.experiment import prerequisite_gate
from training.phase2.metrics import metrics
from training.phase2.models import calibrate, predict, train, vector
from training.phase2.protocol import ABSTAIN
from training.router.common import digest, write

LABELS = ["artifact.modify", "reason.plan"]


def row(record_id="aa", text="edit source package file", label="artifact.modify", split="train", reason="route"):
    return {
        "schema": "blux.routing-record",
        "version": 1,
        "id": record_id,
        "text": text,
        "label": label,
        "reason": reason,
        "split": split,
        "semantic_family": record_id,
        "template_family": None,
        "paraphrase_family": None,
        "phenomena": ["ordinary"] if reason == "route" else [reason],
        "provenance": {
            "method": "test-fixture",
            "authors": ["test-control"],
            "rights": "synthetic-test-only",
            "reviewer": "test-control",
            "approved": True,
            "test_only": True,
            "source": {"repository": "test/control", "commit": "a" * 40, "path": "fixture", "sha256": "b" * 64},
        },
    }


@pytest.fixture
def train_rows():
    return [
        row(),
        row("bb", "modify module path code"),
        row("cc", "plan tasks before launch", "reason.plan"),
        row("dd", "schedule dependent work stages", "reason.plan"),
    ]


@pytest.fixture
def validation_rows():
    return [
        row("ee", "edit configuration contents carefully", split="validation"),
        row("ff", "plan release jobs tomorrow", "reason.plan", "validation"),
    ]


@pytest.fixture
def envelope(train_rows, validation_rows):
    model = train(train_rows, LABELS, [], "word-char-threshold", allow_test=True)
    model, _ = calibrate(model, validation_rows, LABELS, [], allow_test=True)
    return pack(model)


def pack(model):
    m = {
        "schema": "blux.router02-artifact",
        "schema_version": 1,
        "model_id": "blux-router-0.2",
        "model_version": "0.2.0",
        "stage": "development-only",
        "promotion_eligible": False,
        "authoritative_capabilities": LABELS,
        "models": {model["architecture"]: model},
        "dataset_sha256": "a" * 64,
        "source_commits": {"test": "b" * 40},
        "code_sha256": "c" * 64,
    }
    return {"payload": m, "sha256": digest(m)}


def rehash(envelope):
    envelope["sha256"] = digest(envelope["payload"])
    return envelope


def test_fixtures_not_production():
    with pytest.raises(ValueError, match="test fixture"):
        check_row(row(), LABELS, [])


@pytest.mark.parametrize("split", ["test", "adversarial", "regression", "holdout"])
def test_protected_fit(split):
    with pytest.raises(ValueError, match="protected"):
        fit_rows([row(split=split)], split, LABELS, [], allow_test=True)


def test_wrong_role():
    with pytest.raises(ValueError, match="wrong-role"):
        fit_rows([row(split="holdout")], "train", LABELS, [], allow_test=True)


def test_canary():
    with pytest.raises(ValueError, match="canary"):
        check_row(row(text="edit CANARY file"), LABELS, ["CANARY"], allow_test=True)


def test_missing_provenance():
    r = row()
    r["provenance"]["authors"] = []
    with pytest.raises(jsonschema.ValidationError):
        check_row(r, LABELS, [], allow_test=True)


def test_no_model_prose_as_owner_seed():
    r = row()
    r["provenance"].update(method="owner-authored", test_only=False, authors=["Codex"])
    with pytest.raises(ValueError, match="model-authored"):
        check_row(r, LABELS, [])


def test_unknown_capability():
    with pytest.raises(jsonschema.ValidationError):
        check_row(row(label="unavailable.execute"), LABELS, [], allow_test=True)


def test_semantic_family_leak():
    a, b = row(), row("bb", "plan independent software rollout", "reason.plan", "validation")
    b["semantic_family"] = a["semantic_family"]
    with pytest.raises(ValueError, match="family crosses"):
        verify_rows([a, b], LABELS, [], [], allow_test=True)


@pytest.mark.parametrize("kind", ["template_family", "paraphrase_family"])
def test_other_family_leak(kind):
    a, b = row(), row("bb", "plan independent software rollout", "reason.plan", "validation")
    a[kind] = b[kind] = "shared"
    with pytest.raises(ValueError, match="family crosses"):
        verify_rows([a, b], LABELS, [], [], allow_test=True)


def test_duplicate_paraphrase_leak():
    a, b = row(), row("bb", "EDIT SOURCE PACKAGE FILE", split="validation")
    with pytest.raises(ValueError, match="paraphrase crosses"):
        verify_rows([a, b], LABELS, [], [], allow_test=True)


def test_trivial_variants_not_diversity():
    a, b = row(), row("bb", text="edit source package file")
    with pytest.raises(ValueError, match="distinct semantic families"):
        verify_rows([a, b], LABELS, [], [], allow_test=True)


def test_phase1_protected_quarantine():
    with pytest.raises(ValueError, match="Phase-1 protected"):
        verify_rows([row()], LABELS, [], [signature("edit source package file")], allow_test=True)


def test_no_fresh_protected_source_reuse():
    r = row(split="test")
    r["provenance"]["method"] = "source-record"
    with pytest.raises(ValueError, match="must be fresh"):
        check_row(r, LABELS, [], allow_test=True)


def test_ood_cannot_be_capability():
    r = row(reason="out-of-domain")
    with pytest.raises(ValueError, match="ABSTAIN|OOD"):
        check_row(r, LABELS, [], allow_test=True)


def test_abstention_removal_detected(train_rows):
    report = coverage(train_rows, LABELS)
    assert not report["ready"]
    assert any("train:ABSTAIN:out-of-domain" in p for p in report["problems"])
    assert prerequisite_gate(report)["passed"] is False


def test_coverage_counts_real_families():
    a, b = row(), row("bb", text="modify another package layout")
    b["semantic_family"] = a["semantic_family"]
    r = coverage([a, b], LABELS)["counts"]["train"]["classes"]["artifact.modify"]
    assert r == {"records": 2, "semantic_families": 1}


@pytest.fixture
def dataset(tmp_path, train_rows, validation_rows):
    out = tmp_path / "dataset"
    manifest = build(train_rows + validation_rows, out, LABELS, [], [], {}, allow_test=True)
    return out, manifest


def test_deterministic_dataset_build(dataset, tmp_path, train_rows, validation_rows):
    out, m = dataset
    other = tmp_path / "other"
    same = build(train_rows + validation_rows, other, LABELS, [], [], {}, allow_test=True)
    assert same == m
    assert all((out / name).read_bytes() == (other / name).read_bytes() for name in m["files"])
    rows, _ = load_verified(out, digest(m), LABELS, [], [], allow_test=True)
    assert len(rows) == 6
    with pytest.raises(ValueError, match="already exists"):
        build(train_rows, other, LABELS, [], [], {}, allow_test=True)


def test_stale_dataset_digest(dataset):
    out, _ = dataset
    with pytest.raises(ValueError, match="stale dataset digest"):
        load_verified(out, "0" * 64, LABELS, [], [], allow_test=True)


def test_split_lock(dataset):
    out, m = dataset
    (out / "splits.lock.json").write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        load_verified(out, digest(m), LABELS, [], [], allow_test=True)


def test_artifact_checksum(envelope):
    envelope["payload"]["models"]["word-char-threshold"]["threshold"] = 0.33
    with pytest.raises(ValueError, match="checksum"):
        validate(envelope, LABELS, allow_development=True)


def test_model_artifact_mismatch(envelope):
    envelope["payload"]["model_id"] = "blux-router-0.1"
    with pytest.raises(ValueError, match="artifact/model"):
        validate(rehash(envelope), LABELS, allow_development=True)


def test_calibration_metadata(envelope):
    envelope["payload"]["models"]["word-char-threshold"]["temperature"] = 2.0
    with pytest.raises(ValueError, match="calibration metadata"):
        validate(rehash(envelope), LABELS, allow_development=True)


def test_unsupported_not_supported(envelope):
    caps = [*LABELS, "repo.patch"]
    payload = envelope["payload"]
    payload["authoritative_capabilities"] = caps
    m = payload["models"]["word-char-threshold"]
    m["supported_labels"] = [c if c != "reason.plan" else "repo.patch" for c in m["supported_labels"]]
    m["heads"]["direct"]["labels"] = list(m["supported_labels"])
    m["training_counts"]["repo.patch"] = 0
    with pytest.raises(ValueError, match="unsupported class"):
        validate(rehash(envelope), caps, allow_development=True)


def test_development_default_fallback(envelope, tmp_path):
    model = envelope["payload"]["models"]["word-char-threshold"]
    model["threshold"] = model["calibration"]["threshold"] = 0.0
    p = tmp_path / "model.json"
    write(p, rehash(envelope))
    calls = []

    def fallback():
        calls.append("old-provider")
        return "fallback"

    assert advisory_or_fallback(p, "edit source package file", LABELS, fallback) == "fallback"
    assert calls == ["old-provider"]


@pytest.mark.parametrize("fault", ["missing", "malformed", "corrupt", "version", "unknown", "abstain"])
def test_artifact_fallback(envelope, tmp_path, fault):
    p = tmp_path / "artifact.json"
    if fault == "version":
        envelope["payload"]["schema_version"] = 2
    if fault == "unknown":
        envelope["payload"]["authoritative_capabilities"] = ["made.up"]
    if fault != "missing":
        write(p, rehash(envelope))
    if fault == "corrupt":
        p.write_text("{bad")
    if fault == "malformed":
        p.write_text("[]")
    assert advisory_or_fallback(p, "", LABELS, lambda: "safe", allow_development=True) == "safe"


def test_explicit_ood_requires_examples(train_rows):
    with pytest.raises(ValueError, match="requires abstention"):
        train(train_rows, LABELS, [], "word-char-explicit-abstain", allow_test=True)


def test_explicit_abstention_path(train_rows, validation_rows):
    # Deliberately synthetic fixture, never production corpus.
    training = [*train_rows, row("gg", "fixture outside any route", ABSTAIN, reason="out-of-domain")]
    validation = [
        *validation_rows,
        row("hh", "fixture unsupported unusual demand", ABSTAIN, "validation", "unsupported"),
    ]
    model = train(training, LABELS, [], "word-char-explicit-abstain", allow_test=True)
    model, _ = calibrate(model, validation, LABELS, [], allow_test=True)
    assert ABSTAIN in model["supported_labels"]
    assert validate(pack(model), LABELS, allow_development=True)
    result = metrics(validation, [predict(model, r["text"]) for r in validation], LABELS)
    assert result["abstention"]["truth_count"] == 1
    assert result["behavior"]["unsupported"]["n"] == 1


def test_deterministic_training_and_unicode_parity(train_rows):
    first = train(train_rows, LABELS, [], "word-char-threshold", allow_test=True)
    second = train(train_rows, LABELS, [], "word-char-threshold", allow_test=True)
    assert first == second
    assert vector(first, "EDIT\t  source") == vector(second, "EDIT\t  source")
    assert predict(first, "edit") == predict(second, "edit")


def test_metrics_do_not_invent_ood_evidence(train_rows):
    output = predictions(train_rows, train_rows, LABELS, "majority")
    result = metrics(train_rows, output, LABELS)
    assert result["abstention"]["truth_count"] == 0
    assert result["abstention"]["recall"] is None
    assert result["abstention"]["roc_auc"] is None
    assert result["behavior"]["unsupported"]["n"] == 0


def test_calibration_cannot_claim_safety_without_negatives(envelope):
    model = envelope["payload"]["models"]["word-char-threshold"]
    assert model["calibration"]["target_met"] is False
    assert model["threshold"] == 1.0


def test_logical_split_lock(dataset):
    import hashlib

    from training.router.common import read

    out, m = dataset
    lock = read(out / "splits.lock.json")
    lock[next(iter(lock))]["split"] = "holdout"
    write(out / "splits.lock.json", lock)
    m["files"]["splits.lock.json"] = hashlib.sha256((out / "splits.lock.json").read_bytes()).hexdigest()
    write(out / "manifest.json", m)
    with pytest.raises(ValueError, match="split lock mismatch"):
        load_verified(out, digest(m), LABELS, [], [], allow_test=True)


def test_owner_seed_requires_digest_and_rights(tmp_path):
    from training.phase2.authoring import owner_seeds

    p = tmp_path / "seed.jsonl"
    p.write_text("")
    with pytest.raises(ValueError, match="owner approval"):
        owner_seeds(p, {}, LABELS, [])


def test_template_variants_keep_same_family():
    from training.phase2.authoring import expand_template

    r = row(text="test fixture {slot}")
    r["provenance"]["method"] = "owner-authored"
    # Still test_only: this function's output is not a production corpus.
    variants = expand_template(r, {"slot": ["alpha", "beta"]})
    assert len(variants) == 2
    assert len({v["semantic_family"] for v in variants}) == 1
    assert len({v["template_family"] for v in variants}) == 1
    assert all(v["provenance"]["test_only"] for v in variants)
