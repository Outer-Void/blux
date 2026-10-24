"""Every failure path yields a valid result AND a valid receipt, with an explicit code."""

from __future__ import annotations

import copy
import time
from typing import Any

import pytest

from blux_runtime.builtin import echo, reference_policy
from blux_runtime.executor import InMemoryReceiptSink, Outcome, Runtime
from blux_runtime.registry import CapabilityRegistry
from blux_sdk import envelopes
from blux_sdk.errors import BluxError, ErrorCode
from blux_sdk.schemas import check_envelope


def _assert_failure(outcome: Outcome, code: ErrorCode, status: str) -> dict[str, Any]:
    check_envelope(outcome.result, "result")
    check_envelope(outcome.receipt, "receipt")
    assert outcome.result["status"] == status
    assert outcome.result["output"] is None
    assert outcome.result["error"]["code"] == code.value
    assert outcome.receipt["error_code"] == code.value
    assert outcome.receipt["output_sha256"] is None
    assert outcome.result["receipt_id"] == outcome.receipt["receipt_id"]
    return outcome.result["error"]


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"not json", ErrorCode.MALFORMED_INPUT),
        (b"[]", ErrorCode.MALFORMED_INPUT),
        ('{"contract": "blux.execution", "contract": "blux.execution"}', ErrorCode.MALFORMED_INPUT),
        (b"{}", ErrorCode.INVALID_ENVELOPE),
        (b'{"contract": "blux.result", "contract_version": "1.0"}', ErrorCode.INVALID_ENVELOPE),
    ],
)
def test_unparseable_requests(runtime: Runtime, sink: InMemoryReceiptSink, raw: bytes | str, code: ErrorCode) -> None:
    outcome = runtime.execute(raw)
    _assert_failure(outcome, code, "rejected")
    assert outcome.receipt["policy_decision"]["effect"] == "not_evaluated"
    assert outcome.receipt["actor"] is None
    assert sink.receipts == [outcome.receipt]


@pytest.mark.parametrize(
    ("version", "code"),
    [
        ("2.0", ErrorCode.UNSUPPORTED_CONTRACT_VERSION),
        ("1.1", ErrorCode.UNSUPPORTED_CONTRACT_VERSION),
        ("0.1", ErrorCode.UNSUPPORTED_CONTRACT_VERSION),
        (1.0, ErrorCode.MALFORMED_CONTRACT_VERSION),
        ("1", ErrorCode.MALFORMED_CONTRACT_VERSION),
        ("latest", ErrorCode.MALFORMED_CONTRACT_VERSION),
        (None, ErrorCode.MALFORMED_CONTRACT_VERSION),
    ],
)
def test_contract_versions_fail_explicitly(
    runtime: Runtime, echo_request: dict[str, Any], version: Any, code: ErrorCode
) -> None:
    echo_request["contract_version"] = version
    error = _assert_failure(runtime.execute(echo_request), code, "rejected")
    # The operation id from the request is preserved even though the request was rejected.
    assert error["operation_id"] == echo_request["operation_id"]


def test_unknown_capability(runtime: Runtime, echo_request: dict[str, Any]) -> None:
    echo_request["capability_id"] = "nope.missing"
    outcome = runtime.execute(echo_request)
    _assert_failure(outcome, ErrorCode.UNKNOWN_CAPABILITY, "rejected")
    assert outcome.result["capability_id"] == "nope.missing"
    assert outcome.receipt["capability_version"] is None


def test_capability_version_pin(runtime: Runtime, echo_request: dict[str, Any]) -> None:
    echo_request["capability_version"] = "9.9.9"
    error = _assert_failure(runtime.execute(echo_request), ErrorCode.CAPABILITY_VERSION_MISMATCH, "rejected")
    assert error["details"] == {"requested": "9.9.9", "available": "1.0.0"}
    echo_request["capability_version"] = "1.0.0"
    assert runtime.execute(echo_request).result["status"] == "succeeded"


@pytest.mark.parametrize(
    "bad_input",
    [
        {},
        {"message": 1},
        {"message": "x", "extra": True},
        {"message": "x" * 4097},
        {"message": "x", "uppercase": "yes"},
    ],
)
def test_input_schema_enforced(runtime: Runtime, echo_request: dict[str, Any], bad_input: dict[str, Any]) -> None:
    echo_request["input"] = bad_input
    outcome = runtime.execute(echo_request)
    _assert_failure(outcome, ErrorCode.SCHEMA_VALIDATION_FAILED, "rejected")
    assert outcome.receipt["policy_decision"]["effect"] == "not_evaluated"


def test_policy_denies_by_default(sink: InMemoryReceiptSink, echo_request: dict[str, Any]) -> None:
    policy = {**reference_policy(), "rules": []}
    reg = CapabilityRegistry()
    reg.register(echo.DESCRIPTOR, echo.handle)
    outcome = Runtime(reg, policy, receipt_sink=sink).execute(echo_request)
    error = _assert_failure(outcome, ErrorCode.POLICY_DENIED, "rejected")
    assert error["details"]["effect"] == "deny"
    assert outcome.receipt["policy_decision"]["rule_id"] is None


def _runtime_with(handler: Any, *, output_schema: dict[str, Any] | None = None, timeout_ms: int = 1000) -> Runtime:
    desc = copy.deepcopy(echo.DESCRIPTOR)
    desc["capability_id"] = "fake.cap"
    desc["timeout_ms"] = timeout_ms
    if output_schema is not None:
        desc["output_schema"] = output_schema
    reg = CapabilityRegistry()
    reg.register(desc, handler)
    policy = {
        **reference_policy(),
        "rules": [{"rule_id": "allow-fake", "effect": "allow", "capabilities": ["fake.*"]}],
    }
    return Runtime(reg, policy)


def _fake_request(**kw: Any) -> dict[str, Any]:
    return envelopes.execution_request("fake.cap", {"message": "x"}, actor={"type": "user", "id": "u"}, **kw)


def test_handler_exception_is_normalized_without_leaking() -> None:
    def boom(_input: dict[str, Any], _ctx: Any) -> dict[str, Any]:
        raise RuntimeError("password=hunter2")

    outcome = _runtime_with(boom).execute(_fake_request())
    error = _assert_failure(outcome, ErrorCode.EXECUTION_FAILED, "failed")
    assert error["details"] == {"exception_type": "RuntimeError"}
    assert "hunter2" not in str(outcome.result) + str(outcome.receipt)
    assert outcome.receipt["policy_decision"]["effect"] == "allow"


def test_handler_cannot_impersonate_a_rejection() -> None:
    def fake_deny(_input: dict[str, Any], _ctx: Any) -> dict[str, Any]:
        raise BluxError(ErrorCode.POLICY_DENIED, "pretend")

    error = _assert_failure(_runtime_with(fake_deny).execute(_fake_request()), ErrorCode.EXECUTION_FAILED, "failed")
    assert error["details"] == {"handler_code": "POLICY_DENIED"}


@pytest.mark.parametrize("bad_output", [{"message": "x"}, ["not", "an", "object"], None, "str"])
def test_output_validation(bad_output: Any) -> None:
    outcome = _runtime_with(lambda _i, _c: bad_output).execute(_fake_request())
    _assert_failure(outcome, ErrorCode.OUTPUT_VALIDATION_FAILED, "failed")


def test_timeout_uses_the_smaller_of_request_and_capability() -> None:
    def slow(_input: dict[str, Any], _ctx: Any) -> dict[str, Any]:
        time.sleep(0.5)
        return {"message": "x", "length": 1, "uppercase": False}

    rt = _runtime_with(slow, timeout_ms=5000)
    t0 = time.monotonic()
    outcome = rt.execute(_fake_request(timeout_ms=50))
    assert time.monotonic() - t0 < 0.4
    error = _assert_failure(outcome, ErrorCode.TIMEOUT, "failed")
    assert error["retryable"] is True
    assert error["details"] == {"timeout_ms": 50}


def test_handler_receives_context() -> None:
    seen: dict[str, Any] = {}

    def capture(_input: dict[str, Any], ctx: Any) -> dict[str, Any]:
        seen.update(vars(ctx))
        return {"message": "x", "length": 1, "uppercase": False}

    req = _fake_request(project_id="blux")
    assert _runtime_with(capture).execute(req).result["status"] == "succeeded"
    assert seen["operation_id"] == req["operation_id"]
    assert seen["project_id"] == "blux"
    assert seen["capability_id"] == "fake.cap"
    assert "policy" not in seen


def test_runtime_rejects_invalid_policy() -> None:
    with pytest.raises(BluxError) as info:
        Runtime(CapabilityRegistry(), {**reference_policy(), "default_effect": "allow"})
    assert info.value.code is ErrorCode.SCHEMA_VALIDATION_FAILED
