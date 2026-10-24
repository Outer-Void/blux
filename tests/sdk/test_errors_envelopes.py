from __future__ import annotations

import pytest

from blux_sdk import envelopes, ids
from blux_sdk.capability import CapabilityDescriptor
from blux_sdk.errors import BluxError, ErrorCode, normalize_error
from blux_sdk.schemas import check_envelope, schema_errors
from tests.conftest import load_fixture


def test_every_error_code_is_in_schema() -> None:
    from blux_sdk.schemas import load_schemas

    assert {c.value for c in ErrorCode} == set(load_schemas()["error"]["properties"]["code"]["enum"])


@pytest.mark.parametrize("code", list(ErrorCode))
def test_error_documents_validate(code: ErrorCode) -> None:
    doc = BluxError(code, "x" * 5000, details={"k": 1}).to_document(operation_id=ids.new_operation_id())
    check_envelope(doc, "error")
    assert len(doc["message"]) == 2048
    assert doc["retryable"] is (code is ErrorCode.TIMEOUT)


def test_normalize_hides_exception_text() -> None:
    err = normalize_error(RuntimeError("secret=hunter2"))
    assert err.code is ErrorCode.EXECUTION_FAILED
    assert "hunter2" not in str(err.to_document())
    assert normalize_error(TimeoutError()).code is ErrorCode.TIMEOUT
    original = BluxError(ErrorCode.POLICY_DENIED, "no")
    assert normalize_error(original) is original


def test_schema_errors_do_not_echo_instance_values() -> None:
    doc = load_fixture("valid", "execution", "echo.json")
    doc["operation_id"] = "op_SECRET-VALUE"
    errs = schema_errors("execution", doc)
    assert errs == [{"path": "$.operation_id", "keyword": "pattern"}]
    assert "SECRET" not in str(errs)


def test_execution_request_builder() -> None:
    parent = ids.new_operation_id()
    req = envelopes.execution_request(
        "echo.test",
        {"message": "m"},
        actor={"type": "agent", "id": "a"},
        parent_operation_id=parent,
        session_id=ids.new_session_id(),
        project_id="blux",
        capability_version="1.0.0",
        timeout_ms=10,
        labels={"x": "y"},
    )
    assert req["parent_operation_id"] == parent
    assert ids.is_valid(ids.IdKind.OPERATION, req["operation_id"])


def test_execution_request_builder_rejects_bad_ids() -> None:
    with pytest.raises(BluxError) as info:
        envelopes.execution_request("echo.test", {}, actor={"type": "user", "id": "u"}, session_id="session-1")
    assert info.value.code is ErrorCode.SCHEMA_VALIDATION_FAILED


def test_artifact_from_bytes() -> None:
    art = envelopes.artifact_from_bytes(
        operation_id=ids.new_operation_id(), name="a.txt", media_type="text/plain", data=b"abc"
    )
    assert art["size_bytes"] == 3
    assert art["sha256"] == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_status_mapping() -> None:
    assert envelopes.status_for(BluxError(ErrorCode.POLICY_DENIED, "x")) == "rejected"
    assert envelopes.status_for(BluxError(ErrorCode.EXECUTION_FAILED, "x")) == "failed"
    assert envelopes.status_for(BluxError(ErrorCode.TIMEOUT, "x")) == "failed"


def test_capability_descriptor_roundtrip() -> None:
    doc = load_fixture("valid", "capability", "echo-test.json")
    desc = CapabilityDescriptor.from_document(doc)
    assert desc.to_document() == doc
    desc.validate_input({"message": "ok"})
    with pytest.raises(BluxError) as info:
        desc.validate_input({"message": 5})
    assert info.value.code is ErrorCode.SCHEMA_VALIDATION_FAILED
    with pytest.raises(BluxError) as info:
        desc.validate_output({"message": "x"})
    assert info.value.code is ErrorCode.OUTPUT_VALIDATION_FAILED
