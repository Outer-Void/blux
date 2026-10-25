"""`model.generate` and `model.catalog`: the Model API through the capability runtime (ADR-0010).

Going through the runtime means policy decides who may call a model, the request and response are
validated against the Model API schemas, and every call gets a receipt. Each receipt lists one
artifact: the call's safe metadata (logical model, provider, route, attempts, usage, timing,
determinism; never prompts, output text or credentials), as `application/vnd.blux.model-call+json`.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from blux_runtime.interfaces import ExecutionContext
from blux_runtime.models.router import ModelRouter
from blux_runtime.registry import CapabilityRegistry
from blux_sdk import envelopes, model_api
from blux_sdk.errors import BluxError, ErrorCode
from blux_sdk.model_api import ModelApiError
from blux_sdk.serialization import canonical_json

MEDIA_TYPE = "application/vnd.blux.model-call+json"
PROVIDER = {"repository": "Outer-Void/blux", "kind": "builtin"}


def descriptors() -> list[dict[str, Any]]:
    return [
        {
            "contract": "blux.capability",
            "contract_version": "1.0",
            "capability_id": "model.generate",
            "version": "1.0.0",
            "summary": "Generate with a logical BLUX model (blux-main, blux-fast, blux-reason, blux-code).",
            "contract_versions": ["1.0"],
            "input_schema": model_api.bundled_schema("request"),
            "output_schema": model_api.bundled_schema("response"),
            "side_effects": "external",
            "required_permissions": ["net:model"],
            "idempotent": False,
            "timeout_ms": 600000,
            "provider": {**PROVIDER, "entrypoint": "blux_runtime.models.capability:generate"},
            "labels": {"api": "model-api-v1"},
        },
        {
            "contract": "blux.capability",
            "contract_version": "1.0",
            "capability_id": "model.catalog",
            "version": "1.0.0",
            "summary": "List the logical models and what their routes can provide.",
            "contract_versions": ["1.0"],
            "input_schema": {"type": "object", "additionalProperties": False},
            "output_schema": model_api.bundled_schema("catalog"),
            "side_effects": "none",
            "required_permissions": [],
            "idempotent": True,
            "timeout_ms": 5000,
            "provider": {**PROVIDER, "entrypoint": "blux_runtime.models.capability:catalog"},
            "labels": {"api": "model-api-v1"},
        },
    ]


def default_routes() -> dict[str, Any]:
    text = resources.files("blux_runtime.models").joinpath("default_routes.json").read_text(encoding="utf-8")
    doc: dict[str, Any] = json.loads(text)
    return doc


def model_policy() -> dict[str, Any]:
    text = resources.files("blux_runtime.models").joinpath("model_policy.json").read_text(encoding="utf-8")
    doc: dict[str, Any] = json.loads(text)
    return doc


def register(registry: CapabilityRegistry, router: ModelRouter) -> None:
    by_id = {d["capability_id"]: d for d in descriptors()}

    def generate(input: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:  # noqa: A002
        linkage = {
            "operation_id": context.operation_id,
            "parent_operation_id": context.parent_operation_id,
            "session_id": context.session_id,
            "project_id": context.project_id,
            "capability_id": context.capability_id,
        }
        try:
            response = router.generate(input, on_event=context.emit_event, cancel=context.cancel, linkage=linkage)
        except ModelApiError as exc:
            _attach(context, _failure_record(input, exc))
            raise BluxError(
                ErrorCode.EXECUTION_FAILED,
                exc.message,
                details={"provider_code": exc.code, "model_error": exc.to_document()},
            ) from None
        _attach(context, model_api.safe_metadata(response))
        return response

    def catalog(input: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:  # noqa: A002
        return router.catalog()

    registry.register(by_id["model.generate"], generate)
    registry.register(by_id["model.catalog"], catalog)


def _failure_record(request: Any, exc: ModelApiError) -> dict[str, Any]:
    model = request.get("model") if isinstance(request, dict) else None
    record: dict[str, Any] = model_api.scrub_doc(
        {
            "contract": "blux.model-call",
            "model_api_version": model_api.MODEL_API_VERSION,
            "model": model if model in model_api.LOGICAL_MODELS else None,
            "status": "failed",
            "error_code": exc.code,
            "retryable": exc.retryable,
            "attempts": [
                {
                    k: a[k]
                    for k in (
                        "route_id",
                        "provider",
                        "provider_model",
                        "outcome",
                        "error_code",
                        "latency_ms",
                        "retries",
                    )
                }
                for a in exc.attempts
            ],
        }
    )
    return record


def _attach(context: ExecutionContext, record: dict[str, Any]) -> None:
    data = canonical_json(record).encode("utf-8")
    if record.get("status") == "failed":
        labels = {"status": "failed", "error_code": str(record["error_code"]), "attempts": str(len(record["attempts"]))}
        if record.get("model"):
            labels["model"] = str(record["model"])
    else:
        labels = model_api.metadata_labels(record)
    descriptor = envelopes.artifact_from_bytes(
        operation_id=context.operation_id, name="model-call.json", media_type=MEDIA_TYPE, data=data, labels=labels
    )
    context.attach_artifact(descriptor, data)
