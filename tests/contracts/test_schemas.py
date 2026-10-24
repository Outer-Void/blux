"""Contract v1 schemas: well-formed, complete, packaged without drift, and enforced by fixtures."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from blux_sdk import ids
from blux_sdk.errors import BluxError
from blux_sdk.schemas import KINDS, check_envelope, load_schemas
from tests.conftest import FIXTURES, ROOT, fixture_files

CONTRACTS = ROOT / "contracts" / "v1"
REQUIRED_KINDS = {"capability", "execution", "result", "receipt", "artifact", "model", "project", "policy", "error"}


def canonical_schemas() -> dict[str, dict]:
    return {p.name.removesuffix(".schema.json"): json.loads(p.read_text()) for p in CONTRACTS.glob("*.schema.json")}


def test_all_required_contracts_exist() -> None:
    assert set(canonical_schemas()) >= REQUIRED_KINDS
    assert set(KINDS) >= REQUIRED_KINDS


@pytest.mark.parametrize("name", sorted(canonical_schemas()))
def test_schema_is_valid_draft_2020_12(name: str) -> None:
    schema = canonical_schemas()[name]
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == f"urn:blux:contract:v1:{name}"
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_contract_const_matches_kind(kind: str) -> None:
    schema = canonical_schemas()[kind]
    assert schema["properties"]["contract"] == {"const": KINDS[kind]}
    assert "contract_version" in schema["required"]
    assert schema["additionalProperties"] is False


def test_packaged_schemas_match_canonical() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "packaging" / "sync_schemas.py"), "--check"], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    assert load_schemas() == canonical_schemas()


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_every_kind_has_valid_and_invalid_fixtures(kind: str) -> None:
    assert list((FIXTURES / "valid" / kind).glob("*.json")), kind
    assert len(list((FIXTURES / "invalid" / kind).glob("*.json"))) >= 8, kind


@pytest.mark.parametrize("path", fixture_files("valid"), ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_valid_fixture_passes(path: Path) -> None:
    check_envelope(json.loads(path.read_text()), path.parent.name)


@pytest.mark.parametrize("path", fixture_files("invalid"), ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_invalid_fixture_fails_with_expected_code(path: Path) -> None:
    case = json.loads(path.read_text())
    with pytest.raises(BluxError) as info:
        check_envelope(case["document"], path.parent.name)
    assert info.value.code.value == case["expect"]
    # Errors are themselves valid contract documents.
    check_envelope(info.value.to_document(), "error")


def _schema_id_pattern(defn: str) -> re.Pattern[str]:
    common = canonical_schemas()["common"]["$defs"][defn]
    return re.compile(common["pattern"])


@pytest.mark.parametrize(
    ("kind", "defn"),
    [
        (ids.IdKind.OPERATION, "operation_id"),
        (ids.IdKind.RECEIPT, "receipt_id"),
        (ids.IdKind.ARTIFACT, "artifact_id"),
        (ids.IdKind.SESSION, "session_id"),
        (ids.IdKind.PROJECT, "project_id"),
        (ids.IdKind.CAPABILITY, "capability_id"),
        (ids.IdKind.MODEL, "model_id"),
    ],
)
def test_sdk_id_patterns_agree_with_schema(kind: ids.IdKind, defn: str) -> None:
    samples = [
        "op_01JAAAAAAAAAAAAAAAAAAAAAAA",
        "rcpt_01JAAAAAAAAAAAAAAAAAAAAAAA",
        "art_01JAAAAAAAAAAAAAAAAAAAAAAA",
        "ses_01JAAAAAAAAAAAAAAAAAAAAAAA",
        "op_01JAAAAAAAAAAAAAAAAAAAAAAI",
        "op_short",
        "blux",
        "my-project",
        "a",
        "Echo",
        "echo.test",
        "echo",
        "echo.test.deep_name-2",
        "blux/echo-model",
        "openai/gpt-x:2026",
        "/nope",
        "",
    ]
    if kind in ids.PREFIXES:
        samples += [ids.new_id(kind) for _ in range(5)]
    schema = Draft202012Validator(canonical_schemas()["common"]["$defs"][defn])
    for s in samples:
        assert ids.is_valid(kind, s) == schema.is_valid(s), s
