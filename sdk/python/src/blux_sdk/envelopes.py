"""Builders for operation envelopes (ADR-0004). Every builder returns a schema-valid document or raises."""

from __future__ import annotations

from typing import Any

from blux_sdk import ids
from blux_sdk.errors import REJECTION_CODES, BluxError
from blux_sdk.schemas import check_envelope
from blux_sdk.serialization import sha256_of
from blux_sdk.timeutil import utc_now
from blux_sdk.versions import CONTRACT_VERSION


def _header(kind: str) -> dict[str, Any]:
    return {"contract": f"blux.{kind}", "contract_version": CONTRACT_VERSION}


def execution_request(
    capability_id: str,
    input: dict[str, Any],  # noqa: A002 - matches the contract field name
    *,
    actor: dict[str, str],
    operation_id: str | None = None,
    parent_operation_id: str | None = None,
    session_id: str | None = None,
    project_id: str | None = None,
    capability_version: str | None = None,
    timeout_ms: int | None = None,
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        **_header("execution"),
        "operation_id": operation_id or ids.new_operation_id(),
        "parent_operation_id": parent_operation_id,
        "session_id": session_id,
        "project_id": project_id,
        "capability_id": capability_id,
        "actor": dict(actor),
        "input": input,
        "requested_at": utc_now(),
    }
    if capability_version is not None:
        doc["capability_version"] = capability_version
    if timeout_ms is not None:
        doc["timeout_ms"] = timeout_ms
    if labels:
        doc["labels"] = dict(labels)
    return check_envelope(doc, "execution")


def success_result(
    *,
    operation_id: str,
    capability_id: str,
    output: dict[str, Any],
    receipt_id: str,
    started_at: str,
    finished_at: str,
    duration_ms: int,
    artifact_ids: list[str] | None = None,
) -> dict[str, Any]:
    doc = {
        **_header("result"),
        "operation_id": operation_id,
        "capability_id": capability_id,
        "status": "succeeded",
        "output": output,
        "error": None,
        "artifact_ids": list(artifact_ids or []),
        "receipt_id": receipt_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
    }
    return check_envelope(doc, "result")


def status_for(error: BluxError) -> str:
    """rejected = refused before the handler ran; failed = the handler ran and the operation failed."""
    return "rejected" if error.code in REJECTION_CODES else "failed"


def error_result(
    *,
    operation_id: str,
    capability_id: str | None,
    error: BluxError,
    receipt_id: str,
    started_at: str,
    finished_at: str,
    duration_ms: int,
) -> dict[str, Any]:
    doc = {
        **_header("result"),
        "operation_id": operation_id,
        "capability_id": capability_id,
        "status": status_for(error),
        "output": None,
        "error": error.to_document(operation_id=operation_id),
        "artifact_ids": [],
        "receipt_id": receipt_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
    }
    return check_envelope(doc, "result")


def receipt(
    *,
    receipt_id: str,
    operation_id: str,
    parent_operation_id: str | None,
    session_id: str | None,
    project_id: str | None,
    capability_id: str | None,
    capability_version: str | None,
    actor: dict[str, str] | None,
    status: str,
    error_code: str | None,
    policy_decision: dict[str, Any],
    input_sha256: str,
    output_sha256: str | None,
    artifact_ids: list[str],
    started_at: str,
    finished_at: str,
    issuer: dict[str, str],
) -> dict[str, Any]:
    doc = {
        **_header("receipt"),
        "receipt_id": receipt_id,
        "operation_id": operation_id,
        "parent_operation_id": parent_operation_id,
        "session_id": session_id,
        "project_id": project_id,
        "capability_id": capability_id,
        "capability_version": capability_version,
        "actor": actor,
        "status": status,
        "error_code": error_code,
        "policy_decision": policy_decision,
        "input_sha256": input_sha256,
        "output_sha256": output_sha256,
        "artifact_ids": list(artifact_ids),
        "started_at": started_at,
        "finished_at": finished_at,
        "issuer": issuer,
    }
    return check_envelope(doc, "receipt")


def artifact_from_bytes(
    *,
    operation_id: str,
    name: str,
    media_type: str,
    data: bytes,
    uri: str | None = None,
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        **_header("artifact"),
        "artifact_id": ids.new_artifact_id(),
        "operation_id": operation_id,
        "name": name,
        "media_type": media_type,
        "sha256": sha256_of(data),
        "size_bytes": len(data),
        "created_at": utc_now(),
    }
    if uri is not None:
        doc["uri"] = uri
    if labels:
        doc["labels"] = dict(labels)
    return check_envelope(doc, "artifact")
