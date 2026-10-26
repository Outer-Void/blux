"""Reference executor: execution request -> result + receipt.

Every call returns a schema-valid blux.result and emits a schema-valid blux.receipt, whatever the input.
Nothing is raised to the caller for bad requests; failures are data (ADR-0006).

With a hook bus (ADR-0012) the executor emits the request, tool, reason, capability, artifact, receipt and
runtime events. Hooks may veto the vetoable `before` events, which denies the call. They can never allow
one: policy is evaluated regardless. The hook receipts of an operation are attached to it as an artifact
(`application/vnd.blux.hook-receipts+json`), so they reach BLUX System with the operation receipt.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import blux_runtime.hooks.catalog as hook_catalog
from blux_runtime import __version__
from blux_runtime.hooks.bus import Emission, EventContext, HookBus, HookError
from blux_runtime.interfaces import ExecutionContext, ReceiptSink
from blux_runtime.policy import NOT_EVALUATED, PolicyEngine
from blux_runtime.registry import CapabilityRegistry, RegisteredCapability
from blux_sdk import envelopes, ids
from blux_sdk.errors import BluxError, ErrorCode, normalize_error
from blux_sdk.schemas import check_envelope
from blux_sdk.serialization import canonical_json, loads_json, sha256_of
from blux_sdk.timeutil import utc_now
from blux_sdk.versions import SUPPORTED_CONTRACT_VERSIONS

ISSUER = {"component": "blux-runtime", "version": __version__}
HOOK_RECEIPTS_MEDIA_TYPE = "application/vnd.blux.hook-receipts+json"


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
    hook_receipts: list[dict[str, Any]] | None = None


class Runtime:
    def __init__(
        self,
        registry: CapabilityRegistry,
        policy: dict[str, Any],
        *,
        receipt_sink: ReceiptSink | None = None,
        hooks: HookBus | None = None,
    ) -> None:
        self.registry = registry
        self.policy = PolicyEngine(policy)
        self.receipt_sink: ReceiptSink = receipt_sink or InMemoryReceiptSink()
        self.hooks = hooks
        self.executions = 0

    # lifecycle (optional: execute() also works without start()) -----------------------------------------

    def start(self) -> None:
        self._notify("runtime.starting", {"runtime_version": __version__})
        count = 0
        for entry in self.registry:
            d = entry.descriptor
            self._notify(
                "capability.discovered",
                {"capability_id": d.capability_id, "version": d.version, "side_effects": d.side_effects,
                 "provider_kind": str(d.provider.get("kind", "unknown"))},
            )  # fmt: skip
            count += 1
        self._notify("runtime.started", {"runtime_version": __version__, "capabilities": count})

    def stop(self, reason: str | None = None) -> None:
        self._notify("runtime.stopping", {"runtime_version": __version__, "reason": reason})
        self._notify("runtime.stopped", {"runtime_version": __version__, "executions": self.executions})

    def __enter__(self) -> Runtime:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop("context exit")

    # hooks --------------------------------------------------------------------------------------------

    def _notify(self, name: str, payload: dict[str, Any], trace: _Trace | None = None) -> Emission | None:
        """Emit an event. A broken emission of a vetoable event fails closed; a notify event never breaks the call."""
        if self.hooks is None:
            return None
        try:
            return self.hooks.emit(name, payload, context=_event_context(trace))
        except HookError as exc:
            if hook_catalog.spec(name).vetoable:
                raise BluxError(
                    ErrorCode.INTERNAL_ERROR, "Hook emission failed.", details={"event": name, "reason": str(exc)[:256]}
                ) from None
            return None

    def _guard(self, name: str, payload: dict[str, Any], trace: _Trace) -> Emission | None:
        """A vetoable event: a veto (or an interrupted guard) denies the call."""
        emission = self._notify(name, payload, trace)
        if emission is not None and not emission.proceed:
            raise BluxError(
                ErrorCode.POLICY_DENIED,
                "Denied by hook.",
                details={
                    "denied_by": "hook",
                    "event": name,
                    "hook_id": emission.vetoed_by,
                    "reason": emission.veto_reason or ("cancelled" if emission.cancelled else "guard did not complete"),
                },
            )
        return emission

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
        self.executions += 1
        error: BluxError | None = None
        collect = None
        if self.hooks is not None:
            trace.hook_receipts = []
            receipts = trace.hook_receipts

            def collect(receipt: dict[str, Any]) -> None:
                if receipt["context"].get("operation_id") == trace.operation_id:
                    receipts.append(receipt)

            self.hooks.add_receipt_sink(collect)
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
        try:
            return self._finish(request, trace, error, started_at, finished_at, duration_ms)
        finally:
            if self.hooks is not None and collect is not None:
                self.hooks.remove_receipt_sink(collect)

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
        self._notify(
            "request.received",
            {"capability_id": _safe_capability_id(doc), "input_sha256": _raw_sha256(raw),
             "size_bytes": len(raw) if isinstance(raw, (str, bytes)) else None},
            trace,
        )  # fmt: skip
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
        self._notify(
            "request.normalized",
            {"capability_id": desc.capability_id, "capability_version": desc.version,
             "contract_version": req["contract_version"], "input_sha256": trace.input_sha256},
            trace,
        )  # fmt: skip
        kind = desc.labels.get("kind")
        device = desc.labels.get("device_operation") == "true"
        tool = {"tool": desc.capability_id, "side_effects": desc.side_effects, "device_operation": device}
        if kind == "tool":
            try:
                self._guard(
                    "tool.before_authorize",
                    {**tool, "capability_id": desc.capability_id, "arguments": req["input"]},
                    trace,
                )
            except BluxError as exc:
                if exc.code == ErrorCode.POLICY_DENIED:
                    self._notify(
                        "tool.denied",
                        {
                            "tool": desc.capability_id,
                            "denied_by": "hook",
                            "rule_id": None,
                            "reason": exc.details.get("reason"),
                        },
                        trace,
                    )
                raise

        # Policy decides, always. No hook outcome can turn a deny into an allow.
        decision = self.policy.evaluate(desc, req["actor"])
        trace.policy_decision = decision
        if decision["effect"] != "allow":
            if kind == "tool":
                self._notify(
                    "tool.denied",
                    {
                        "tool": desc.capability_id,
                        "denied_by": "policy",
                        "rule_id": decision["rule_id"],
                        "reason": decision["reason"][:256],
                    },
                    trace,
                )
            raise BluxError(ErrorCode.POLICY_DENIED, "Denied by policy.", details=dict(decision))
        if kind == "tool":
            self._notify(
                "tool.authorized",
                {"tool": desc.capability_id, "policy_id": decision["policy_id"], "rule_id": decision["rule_id"]},
                trace,
            )

        timeout_ms = min(req.get("timeout_ms", desc.timeout_ms), desc.timeout_ms)
        self._guard(
            "capability.before_execute",
            {"capability_id": desc.capability_id, "version": desc.version, "side_effects": desc.side_effects,
             "input_sha256": trace.input_sha256},
            trace,
        )  # fmt: skip
        if kind == "reason":
            self._guard(
                "reason.before", {"capability_id": desc.capability_id, "input_sha256": trace.input_sha256}, trace
            )
        if kind == "tool":
            emission = self._guard(
                "tool.before_execute", {**tool, "timeout_ms": timeout_ms, "arguments": req["input"]}, trace
            )
            if emission is not None:
                timeout_ms = min(timeout_ms, int(emission.payload["timeout_ms"]))  # hooks may only shorten it
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
        t0 = time.monotonic()
        try:
            output = _call_with_timeout(entry, req["input"], context, timeout_ms)
            if not isinstance(output, dict):
                raise BluxError(
                    ErrorCode.OUTPUT_VALIDATION_FAILED,
                    "Capability output must be a JSON object.",
                    details={"field": "output", "type": type(output).__name__},
                )
            desc.validate_output(output)
        except BluxError as exc:
            ms = int((time.monotonic() - t0) * 1000)
            if kind == "tool":
                self._notify(
                    "tool.failed", {"tool": desc.capability_id, "error_code": exc.code.value, "duration_ms": ms}, trace
                )
            if kind == "reason":
                self._notify(
                    "reason.after",
                    {"capability_id": desc.capability_id, "status": "failed", "output_sha256": None},
                    trace,
                )
            self._notify(
                "capability.after_execute",
                {"capability_id": desc.capability_id, "status": "failed", "duration_ms": ms, "output_sha256": None},
                trace,
            )
            raise
        trace.output = output
        ms = int((time.monotonic() - t0) * 1000)
        out_sha = sha256_of(output)
        if kind == "tool":
            self._notify(
                "tool.after_execute",
                {"tool": desc.capability_id, "status": "succeeded", "duration_ms": ms, "output_sha256": out_sha},
                trace,
            )
        if kind == "reason":
            self._notify(
                "reason.after",
                {"capability_id": desc.capability_id, "status": "succeeded", "output_sha256": out_sha},
                trace,
            )
        self._notify(
            "capability.after_execute",
            {"capability_id": desc.capability_id, "status": "succeeded", "duration_ms": ms, "output_sha256": out_sha},
            trace,
        )

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

        if error is None:
            self._notify(
                "request.completed",
                {"capability_id": capability_id, "status": result["status"], "duration_ms": duration_ms,
                 "output_sha256": sha256_of(trace.output)},
                trace,
            )  # fmt: skip
        else:
            self._notify(
                "request.failed",
                {"capability_id": capability_id, "status": result["status"], "error_code": error.code.value,
                 "duration_ms": duration_ms},
                trace,
            )  # fmt: skip
        artifacts = self._artifacts(trace)
        for descriptor, data in artifacts:
            self._notify(
                "artifact.created",
                {"artifact_id": descriptor["artifact_id"], "name": descriptor["name"],
                 "media_type": descriptor["media_type"], "sha256": descriptor["sha256"], "size_bytes": len(data)},
                trace,
            )  # fmt: skip
        if trace.hook_receipts:
            data = canonical_json({"contract": "blux.hook-receipts", "receipts": trace.hook_receipts}).encode("utf-8")
            artifacts.append(
                (
                    envelopes.artifact_from_bytes(
                        operation_id=trace.operation_id,
                        name="hook-receipts.json",
                        media_type=HOOK_RECEIPTS_MEDIA_TYPE,
                        data=data,
                        labels={
                            "hooks_version": hook_catalog.HOOKS_VERSION,
                            "emissions": str(len(trace.hook_receipts)),
                        },
                    ),
                    data,
                )
            )
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
        self._notify(
            "receipt.created",
            {"receipt_id": receipt_id, "operation_id": trace.operation_id, "status": result["status"],
             "error_code": receipt["error_code"], "artifacts": len(receipt["artifact_ids"])},
            trace,
        )  # fmt: skip
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


def _event_context(trace: _Trace | None) -> EventContext:
    if trace is None:
        return EventContext()
    req = trace.request or {}
    actor = req.get("actor") or {}
    return EventContext(
        operation_id=trace.operation_id,
        parent_operation_id=req.get("parent_operation_id"),
        session_id=req.get("session_id"),
        project_id=req.get("project_id"),
        capability_id=req.get("capability_id"),
        actor_type=actor.get("type"),
        actor_id=actor.get("id"),
    )


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
