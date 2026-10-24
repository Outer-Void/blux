"""HARD GATE: echo.test request -> execution -> structured result + receipt."""

from __future__ import annotations

from typing import Any

from blux_runtime.executor import InMemoryReceiptSink, Runtime
from blux_sdk import envelopes, ids
from blux_sdk.schemas import check_envelope
from blux_sdk.serialization import canonical_json, sha256_of
from blux_sdk.timeutil import parse_timestamp


def test_echo_round_trip(runtime: Runtime, sink: InMemoryReceiptSink) -> None:
    session = ids.new_session_id()
    parent = ids.new_operation_id()
    request = envelopes.execution_request(
        "echo.test",
        {"message": "Hello, BLUX", "uppercase": True},
        actor={"type": "user", "id": "tester"},
        session_id=session,
        project_id="blux",
        parent_operation_id=parent,
    )
    outcome = runtime.execute(request)
    result, receipt = outcome.result, outcome.receipt

    check_envelope(result, "result")
    check_envelope(receipt, "receipt")
    assert result["status"] == "succeeded"
    assert result["output"] == {"message": "HELLO, BLUX", "length": 11, "uppercase": True}
    assert result["error"] is None
    assert result["operation_id"] == request["operation_id"]
    assert result["receipt_id"] == receipt["receipt_id"]
    assert parse_timestamp(result["started_at"]) <= parse_timestamp(result["finished_at"])

    assert sink.receipts == [receipt]
    assert receipt["operation_id"] == request["operation_id"]
    assert receipt["parent_operation_id"] == parent
    assert receipt["session_id"] == session
    assert receipt["project_id"] == "blux"
    assert receipt["capability_version"] == "1.0.0"
    assert receipt["actor"] == {"type": "user", "id": "tester"}
    assert receipt["policy_decision"] == {
        "effect": "allow",
        "policy_id": "blux.reference",
        "rule_id": "allow-echo-test",
        "reason": "reference capability; pure and side-effect free",
    }
    assert receipt["input_sha256"] == sha256_of(request["input"])
    assert receipt["output_sha256"] == sha256_of(result["output"])
    assert receipt["issuer"]["component"] == "blux-runtime"


def test_round_trip_over_the_wire(runtime: Runtime, echo_request: dict[str, Any]) -> None:
    """Bytes in, bytes out: the request survives JSON serialization unchanged."""
    outcome = runtime.execute(canonical_json(echo_request).encode())
    assert outcome.result["status"] == "succeeded"
    assert outcome.result["output"]["message"] == "hello"


def test_echo_is_deterministic(runtime: Runtime) -> None:
    outputs = {
        canonical_json(
            runtime.execute(
                envelopes.execution_request("echo.test", {"message": "same"}, actor={"type": "user", "id": "u"})
            ).result["output"]
        )
        for _ in range(5)
    }
    assert len(outputs) == 1
