"""Compatibility guarantees for Contract v1 (ADR-0005)."""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from blux_runtime.builtin import echo, reference_policy
from blux_runtime.executor import Runtime
from blux_runtime.registry import CapabilityRegistry
from blux_sdk import envelopes, ids
from blux_sdk.errors import BluxError, ErrorCode
from blux_sdk.versions import SUPPORTED_CONTRACT_VERSIONS, negotiate
from tests.conftest import ROOT, load_fixture


def test_contract_v1_schemas_are_locked() -> None:
    """Any schema edit must be deliberate: review per ADR-0005, then `sync_schemas.py --write-lock`."""
    sys.path.insert(0, str(ROOT / "packaging"))
    try:
        import sync_schemas
    finally:
        sys.path.pop(0)
    assert (ROOT / "contracts" / "v1" / "CHECKSUMS.sha256").read_text() == sync_schemas.checksums()


def test_frozen_1_0_request_still_executes() -> None:
    """A request written against 1.0 when it was frozen keeps working."""
    request = load_fixture("valid", "execution", "echo.json")
    from blux_runtime.builtin import builtin_registry

    outcome = Runtime(builtin_registry(), reference_policy()).execute(request)
    assert outcome.result["status"] == "succeeded"
    assert outcome.result["operation_id"] == "op_01JAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.mark.parametrize(
    ("kind", "fixture"),
    [("execution", "echo.json"), ("result", "succeeded.json"), ("receipt", "succeeded.json")],
)
def test_builders_emit_the_frozen_shape(kind: str, fixture: str) -> None:
    """SDK output must not grow or lose fields relative to the frozen 1.0 documents."""
    golden = load_fixture("valid", kind, fixture)
    op, rcpt = ids.new_operation_id(), ids.new_receipt_id()
    built: dict[str, Any]
    if kind == "execution":
        built = envelopes.execution_request(
            "echo.test",
            {"message": "x"},
            actor={"type": "user", "id": "u"},
            session_id=ids.new_session_id(),
            project_id="blux",
        )
    elif kind == "result":
        built = envelopes.success_result(
            operation_id=op,
            capability_id="echo.test",
            output={},
            receipt_id=rcpt,
            started_at="2026-01-01T00:00:00.000Z",
            finished_at="2026-01-01T00:00:00.000Z",
            duration_ms=0,
        )
    else:
        built = envelopes.receipt(
            receipt_id=rcpt, operation_id=op, parent_operation_id=None, session_id=None, project_id=None,
            capability_id="echo.test", capability_version="1.0.0", actor={"type": "user", "id": "u"},
            status="succeeded", error_code=None,
            policy_decision={"effect": "allow", "policy_id": "p", "rule_id": "r", "reason": "x"},
            input_sha256="0" * 64, output_sha256="0" * 64, artifact_ids=[],
            started_at="2026-01-01T00:00:00.000Z", finished_at="2026-01-01T00:00:00.000Z",
            issuer={"component": "c", "version": "0.1.0"},
        )  # fmt: skip
    assert set(built) == set(golden)
    assert built["contract_version"] == golden["contract_version"] == "1.0"


def test_peer_negotiation() -> None:
    assert negotiate(["1.0", "1.1"]) == "1.0"
    assert negotiate(["0.9", "1.0"]) == "1.0"
    with pytest.raises(BluxError) as info:
        negotiate(["2.0"])
    assert info.value.code is ErrorCode.UNSUPPORTED_CONTRACT_VERSION


def test_capability_that_only_speaks_a_future_minor_is_refused() -> None:
    desc = copy.deepcopy(echo.DESCRIPTOR)
    desc["contract_versions"] = ["1.1"]
    reg = CapabilityRegistry()
    reg.register(desc, echo.handle)
    request = envelopes.execution_request("echo.test", {"message": "x"}, actor={"type": "user", "id": "u"})
    outcome = Runtime(reg, reference_policy()).execute(request)
    assert outcome.result["status"] == "rejected"
    assert outcome.result["error"]["code"] == "UNSUPPORTED_CONTRACT_VERSION"
    assert outcome.result["error"]["details"] == {"received": "1.0", "supported": ["1.1"]}


def test_supported_set_is_exactly_1_0() -> None:
    """Widening this set is a contract change and must come with schemas, fixtures and an ADR update."""
    assert SUPPORTED_CONTRACT_VERSIONS == ("1.0",)


def test_schemas_load_outside_the_repository(tmp_path: Path) -> None:
    """Schemas come from package data, not the cwd (blux-system Stage 00 defect). See packaging/verify_wheel.sh."""
    code = (
        "import blux_sdk, pathlib;"
        "from blux_sdk.schemas import load_schemas;"
        "assert 'execution' in load_schemas();"
        "print(pathlib.Path(blux_sdk.__file__).parent)"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
