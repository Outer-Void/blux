"""Reference executor: execution request -> result + receipt.

Every call returns a schema-valid blux.result and emits a schema-valid blux.receipt, whatever the input.
Nothing is raised to the caller for bad requests; failures are data (ADR-0006).
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from blux_runtime import __version__
from blux_runtime.interfaces import ExecutionContext, ReceiptSink
from blux_runtime.policy import NOT_EVALUATED, PolicyEngine
from blux_runtime.registry import CapabilityRegistry, RegisteredCapability
from blux_sdk import envelopes, ids
from blux_sdk.errors import BluxError, ErrorCode, normalize_error
from blux_sdk.schemas import check_envelope
from blux_sdk.serialization import loads_json, sha256_of
from blux_sdk.timeutil import utc_now
from blux_sdk.versions import SUPPORTED_CONTRACT_VERSIONS

ISSUER = {"component": "blux-runtime", "version": __version__}


class InMemoryReceiptSink:
    def __init__(self) -> None:
        self.receipts: list[dict[str, Any]] = []
        self.artifacts: list[tuple[dict[str, Any], bytes]] = []

    def emit(self, receipt: dict[str, Any]) -> None:
        self.receipts.append(receipt)

    def emit_artifact(self, descriptor: dict[str, Any], data: bytes) -> None:
        self.artifacts.append((descriptor, data))


@dataclass(frozen=True)
class Outcome:
    result: dict[str, Any]
    receipt: dict[str, Any]
    # Set when the receipt sink failed. The result is still authoritative; durable recording did not happen.
    sink_error: dict[str, Any] | None = None


@dataclass
class _Trace:
    operation_id: str
    request: dict[str, Any] | None = None
    entry: RegisteredCapability | None = None
    policy_decision: dict[str, Any] | None = None
    input_sha256: str | None = None
    output: dict[str, Any] | None = None
    context: ExecutionContext | None = None


class Runtime:
    def __init__(
        self,
        registry: CapabilityRegistry,
        policy: dict[str, Any],
        *,
        receipt_sink: ReceiptSink | None = None,
    ) -> None:
        self.registry = registry
        self.policy = PolicyEngine(policy)
        self.receipt_sink: ReceiptSink = receipt_sink or InMemoryReceiptSink()

    def execute(
        self,
        request: Mapping[str, Any] | str | bytes,
        *,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> Outcome:
        """Execute one request. `on_event` receives handler progress events; setting `cancel` asks the
        handler to stop (it then fails with its own code, e.g. a Model API CANCELLED)."""
        started_at = utc_now()
        t0 = time.monotonic()
        trace = _Trace(operation_id=ids.new_operation_id())
        error: BluxError | None = None
        try:
            self._run(request, trace, on_event, cancel)
        except BluxError as exc:
            error = exc
        except Exception as exc:  # noqa: BLE001 - runtime defect; still answer with a contract document
            error = BluxError(
                ErrorCode.INTERNAL_ERROR, "Internal runtime error.", details={"exception_type": type(exc).__name__}
            )
        finished_at = utc_now()
        duration_ms = int((time.monotonic() - t0) * 1000)
        return self._finish(request, trace, error, started_at, finished_at, duration_ms)

    def _run(
        self,
        raw: Mapping[str, Any] | str | bytes,
        trace: _Trace,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> None:
        doc: Any = loads_json(raw) if isinstance(raw, (str, bytes)) else raw
        if isinstance(doc, Mapping) and ids.is_valid(ids.IdKind.OPERATION, doc.get("operation_id")):
            trace.operation_id = doc["operation_id"]
        req = check_envelope(doc, "execution", supported=SUPPORTED_CONTRACT_VERSIONS)
        trace.request = req
        trace.input_sha256 = sha256_of(req["input"])

        entry = self.registry.get(req["capability_id"])
        trace.entry = entry
        desc = entry.descriptor
        if req["contract_version"] not in desc.contract_versions:
            raise BluxError(
                ErrorCode.UNSUPPORTED_CONTRACT_VERSION,
                f"{desc.capability_id} does not accept contract_version {req['contract_version']}.",
                details={"received": req["contract_version"], "supported": list(desc.contract_versions)},
            )
        pinned = req.get("capability_version")
        if pinned is not None and pinned != desc.version:
            raise BluxError(
                ErrorCode.CAPABILITY_VERSION_MISMATCH,
                "Requested capability_version is not available.",
                details={"requested": pinned, "available": desc.version},
            )
        desc.validate_input(req["input"])

        decision = self.policy.evaluate(desc, req["actor"])
        trace.policy_decision = decision
        if decision["effect"] != "allow":
            raise BluxError(ErrorCode.POLICY_DENIED, "Denied by policy.", details=dict(decision))

        timeout_ms = min(req.get("timeout_ms", desc.timeout_ms), desc.timeout_ms)
        context = ExecutionContext(
            operation_id=req["operation_id"],
            parent_operation_id=req["parent_operation_id"],
            session_id=req["session_id"],
            project_id=req["project_id"],
            actor=dict(req["actor"]),
            capability_id=desc.capability_id,
            timeout_ms=timeout_ms,
            on_event=on_event,
            cancel=cancel or threading.Event(),
        )
        trace.context = context
        output = _call_with_timeout(entry, req["input"], context, timeout_ms)
        if not isinstance(output, dict):
            raise BluxError(
                ErrorCode.OUTPUT_VALIDATION_FAILED,
                "Capability output must be a JSON object.",
                details={"field": "output", "type": type(output).__name__},
            )
        desc.validate_output(output)
        trace.output = output

    def _finish(
        self,
        raw: Mapping[str, Any] | str | bytes,
        trace: _Trace,
        error: BluxError | None,
        started_at: str,
        finished_at: str,
        duration_ms: int,
    ) -> Outcome:
        receipt_id = ids.new_receipt_id()
        req = trace.request
        desc = trace.entry.descriptor if trace.entry else None
        capability_id = req["capability_id"] if req else _safe_capability_id(raw)

        if error is None:
            assert trace.output is not None and desc is not None
            result = envelopes.success_result(
                operation_id=trace.operation_id,
                capability_id=desc.capability_id,
                output=trace.output,
                receipt_id=receipt_id,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )
        else:
            result = envelopes.error_result(
                operation_id=trace.operation_id,
                capability_id=capability_id,
                error=error,
                receipt_id=receipt_id,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )

        artifacts = self._artifacts(trace)
        receipt = envelopes.receipt(
            receipt_id=receipt_id,
            operation_id=trace.operation_id,
            parent_operation_id=req["parent_operation_id"] if req else None,
            session_id=req["session_id"] if req else None,
            project_id=req["project_id"] if req else None,
            capability_id=capability_id,
            capability_version=desc.version if desc else None,
            actor=dict(req["actor"]) if req else None,
            status=result["status"],
            error_code=error.code.value if error else None,
            policy_decision=trace.policy_decision or dict(NOT_EVALUATED),
            input_sha256=trace.input_sha256 or _raw_sha256(raw),
            output_sha256=sha256_of(trace.output) if trace.output is not None and error is None else None,
            artifact_ids=[a["artifact_id"] for a, _data in artifacts],
            started_at=started_at,
            finished_at=finished_at,
            issuer=dict(ISSUER),
        )
        sink_error: dict[str, Any] | None = None
        try:
            emit_artifact = getattr(self.receipt_sink, "emit_artifact", None)
            if callable(emit_artifact):
                for descriptor, data in artifacts:
                    emit_artifact(descriptor, data)
            self.receipt_sink.emit(receipt)
        except Exception as exc:  # noqa: BLE001 - a sink failure must never lose the result
            to_doc = getattr(exc, "to_document", None)
            sink_error = to_doc() if callable(to_doc) else {"exception_type": type(exc).__name__}
        return Outcome(result=result, receipt=receipt, sink_error=sink_error)

    def _artifacts(self, trace: _Trace) -> list[tuple[dict[str, Any], bytes]]:
        """Artifacts the handler attached that are valid descriptors of this operation's bytes."""
        if trace.context is None:
            return []
        kept = []
        for descriptor, data in trace.context.artifacts:
            try:
                doc = check_envelope(descriptor, "artifact", supported=SUPPORTED_CONTRACT_VERSIONS)
            except BluxError:
                continue
            if doc["operation_id"] == trace.operation_id and doc["sha256"] == sha256_of(data):
                kept.append((doc, data))
        return kept


def _call_with_timeout(
    entry: RegisteredCapability, input_doc: dict[str, Any], context: ExecutionContext, timeout_ms: int
) -> Any:
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"blux-{context.capability_id}")
    future = pool.submit(entry.handler, input_doc, context)
    try:
        return future.result(timeout=timeout_ms / 1000)
    except concurrent.futures.TimeoutError:
        context.cancel.set()  # ask the handler to stop (e.g. kill a model backend)
        raise BluxError(
            ErrorCode.TIMEOUT,
            f"Capability exceeded {timeout_ms} ms.",
            details={"timeout_ms": timeout_ms},
        ) from None
    except BluxError as exc:
        # Handlers may only fail as execution failures; they cannot impersonate rejections.
        if exc.code in (ErrorCode.EXECUTION_FAILED, ErrorCode.TIMEOUT, ErrorCode.OUTPUT_VALIDATION_FAILED):
            raise
        raise BluxError(ErrorCode.EXECUTION_FAILED, exc.message, details={"handler_code": exc.code.value}) from None
    except Exception as exc:  # noqa: BLE001 - normalize all handler failures
        raise normalize_error(exc) from None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _safe_capability_id(raw: Any) -> str | None:
    if isinstance(raw, Mapping) and ids.is_valid(ids.IdKind.CAPABILITY, raw.get("capability_id")):
        return str(raw["capability_id"])
    return None


def _raw_sha256(raw: Any) -> str:
    if isinstance(raw, str):
        return sha256_of(raw.encode("utf-8"))
    if isinstance(raw, bytes):
        return sha256_of(raw)
    try:
        return sha256_of(dict(raw))
    except (TypeError, ValueError):
        return sha256_of(repr(type(raw)).encode("utf-8"))
