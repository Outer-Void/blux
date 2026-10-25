"""BLUX Model API v1: logical models, documents, validation and normalized errors (docs/model-api/v1.md).

Callers target a logical model (blux-main, blux-fast, blux-reason, blux-code). Which provider serves it is
an operator decision recorded in a blux.model-routes table; callers never name a vendor. The schemas
under blux_sdk/schemas/model_api/v1 are a byte-identical copy of contracts/model-api/v1.
"""

from __future__ import annotations

import functools
import json
import re
from collections.abc import Mapping
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource

from blux_sdk import ids

MODEL_API_VERSION = "1.0"
LOGICAL_MODELS = ("blux-main", "blux-fast", "blux-reason", "blux-code")
MODEL_PURPOSE = {
    "blux-main": "General-purpose default: balanced quality, cost and latency.",
    "blux-fast": "Low latency and low cost for short, simple tasks.",
    "blux-reason": "Multi-step reasoning, planning and analysis where latency matters less.",
    "blux-code": "Code generation, review and repair.",
}
CAPABILITIES = ("streaming", "tools", "structured_output", "deterministic", "long_context", "local_only")
KINDS = {
    "request": "blux.model-request",
    "response": "blux.model-response",
    "error": "blux.model-error",
    "stream-event": "blux.model-stream-event",
    "routes": "blux.model-routes",
    "catalog": "blux.model-catalog",
}

# code -> (retryable: the same request may succeed later, fallback: another route may succeed now)
ERRORS: dict[str, tuple[bool, bool]] = {
    "INVALID_REQUEST": (False, False),
    "UNKNOWN_MODEL": (False, False),
    "CAPABILITY_UNAVAILABLE": (False, False),
    "PROVIDER_NOT_CONFIGURED": (False, True),
    "PROVIDER_AUTH_FAILED": (False, True),
    "PROVIDER_RATE_LIMITED": (True, True),
    # Billing or quota exhausted on this provider account: another provider may serve the request now.
    "PROVIDER_QUOTA_EXCEEDED": (False, True),
    "PROVIDER_UNAVAILABLE": (True, True),
    "PROVIDER_ERROR": (True, True),
    "PROVIDER_TIMEOUT": (True, True),
    "PROVIDER_REJECTED": (False, False),
    "CONTEXT_TOO_LONG": (False, True),
    # A provider's safety refusal is final: BLUX never routes around it by asking another provider.
    "CONTENT_FILTERED": (False, False),
    "STRUCTURED_OUTPUT_INVALID": (True, True),
    "TOOL_CALL_INVALID": (True, True),
    "BACKEND_PROTOCOL_ERROR": (True, True),
    "ALL_ROUTES_FAILED": (True, False),
    "TIMEOUT": (True, False),
    "CANCELLED": (False, False),
    "INTERNAL_ERROR": (False, False),
}

# Credential-shaped substrings scrubbed from every provider-supplied message before it leaves a backend
# or the router (the same families blux-system redacts).
SECRET_PATTERNS = (
    r"sk-ant-[A-Za-z0-9_-]{16,}",
    r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}",
    r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}",
    r"(?:AKIA|ASIA)[0-9A-Z]{16}",
    r"AIza[0-9A-Za-z_-]{35}",
    r"xox[baprs]-[A-Za-z0-9-]{10,}",
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
    r"\b[Bb][Ee][Aa][Rr][Ee][Rr]\s+[A-Za-z0-9._~+/=-]{12,}",
    r"(?:[Aa][Pp][Ii][_-]?[Kk][Ee][Yy]|[Xx]-[Aa][Pp][Ii]-[Kk][Ee][Yy]|[Aa]uthorization)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]{12,}",
    r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)",
)
_SECRET_RE = re.compile("|".join(f"(?:{p})" for p in SECRET_PATTERNS))


class ModelApiError(Exception):
    """A normalized Model API failure. Its document is safe to log, return and record."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        provider: str | None = None,
        route_id: str | None = None,
        attempts: list[dict[str, Any]] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if code not in ERRORS:
            code, message = "INTERNAL_ERROR", f"unknown error code {code!r}: {message}"
        super().__init__(message)
        self.code = code
        self.message = scrub(message)[:512]
        self.provider = provider
        self.route_id = route_id
        self.attempts = list(attempts or [])
        self.details = scrub_doc(dict(details or {}))

    @property
    def retryable(self) -> bool:
        return ERRORS[self.code][0]

    @property
    def fallback(self) -> bool:
        return ERRORS[self.code][1]

    def to_document(self) -> dict[str, Any]:
        return {
            "contract": KINDS["error"],
            "model_api_version": MODEL_API_VERSION,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "fallback": self.fallback,
            "provider": self.provider,
            "route_id": self.route_id,
            "attempts": self.attempts,
            "details": self.details,
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> ModelApiError:
        return cls(
            str(doc.get("code")),
            str(doc.get("message", "")),
            provider=doc.get("provider"),
            route_id=doc.get("route_id"),
            attempts=list(doc.get("attempts") or []),
            details=dict(doc.get("details") or {}),
        )


def scrub(text: str) -> str:
    return _SECRET_RE.sub("[redacted]", text)


def scrub_doc(doc: Any) -> Any:
    if isinstance(doc, str):
        return scrub(doc)
    if isinstance(doc, list):
        return [scrub_doc(v) for v in doc]
    if isinstance(doc, dict):
        return {k: scrub_doc(v) for k, v in doc.items()}
    return doc


# ---------------------------------------------------------------------------------------------- schemas


@functools.lru_cache(maxsize=1)
def _schemas() -> dict[str, dict[str, Any]]:
    root = resources.files("blux_sdk").joinpath("schemas").joinpath("model_api").joinpath("v1")
    return {
        e.name.removesuffix(".schema.json"): json.loads(e.read_text(encoding="utf-8"))
        for e in root.iterdir()
        if e.name.endswith(".schema.json")
    }


@functools.lru_cache(maxsize=1)
def _registry() -> Registry:
    reg: Registry = Registry()
    for schema in _schemas().values():
        reg = reg.with_resource(schema["$id"], Resource.from_contents(schema))
    return reg


@functools.cache
def _validator(kind: str) -> Draft202012Validator:
    return Draft202012Validator(_schemas()[kind], registry=_registry())


def schema_errors(kind: str, doc: Any) -> list[str]:
    errors = sorted(_validator(kind).iter_errors(doc), key=lambda e: (list(map(str, e.absolute_path)), e.validator))
    return [f"$/{'/'.join(map(str, e.absolute_path))}: {e.validator}" for e in errors[:20]]


def validate(kind: str, doc: Any, *, error_code: str = "BACKEND_PROTOCOL_ERROR") -> dict[str, Any]:
    errors = schema_errors(kind, doc)
    if errors:
        raise ModelApiError(error_code, f"Not a valid {KINDS[kind]} document: {errors[0]}", details={"errors": errors})
    return dict(doc)


def bundled_schema(kind: str) -> dict[str, Any]:
    """A self-contained copy of a schema (shared definitions inlined), for embedding in capability descriptors."""
    import copy

    schemas = _schemas()
    defs: dict[str, Any] = {}

    def common_def(key: str) -> str:
        if key not in defs:
            defs[key] = None
            defs[key] = rewrite(copy.deepcopy(schemas["common"]["$defs"][key]), in_common=True)
        return f"#/$defs/{key}"

    def rewrite(node: Any, in_common: bool = False) -> Any:
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k == "$ref" and in_common and isinstance(v, str) and v.startswith("#/$defs/"):
                    out[k] = common_def(v.rsplit("/", 1)[-1])
                elif k == "$ref" and isinstance(v, str) and v.startswith("urn:blux:model-api:v1:"):
                    name, _, pointer = v[len("urn:blux:model-api:v1:") :].partition("#")
                    if name == "common":
                        out[k] = common_def(pointer.rsplit("/", 1)[-1])
                    else:
                        key = f"_{name.replace('-', '_')}"
                        if key not in defs:
                            defs[key] = None
                            body = {kk: vv for kk, vv in schemas[name].items() if kk not in ("$schema", "$id")}
                            defs[key] = rewrite(copy.deepcopy(body))
                        out[k] = f"#/$defs/{key}"
                else:
                    out[k] = rewrite(v, in_common)
            return out
        if isinstance(node, list):
            return [rewrite(v, in_common) for v in node]
        return node

    body = rewrite(copy.deepcopy({k: v for k, v in schemas[kind].items() if k not in ("$schema", "$id")}))
    return {**body, "$defs": defs}


# ---------------------------------------------------------------------------------------------- requests


def request(
    model: str, prompt: str | None = None, *, messages: list[dict[str, Any]] | None = None, **fields: Any
) -> dict[str, Any]:
    """Build a blux.model-request. `prompt` is shorthand for one user message."""
    msgs = list(messages or []) + ([{"role": "user", "content": prompt}] if prompt is not None else [])
    return {
        "contract": KINDS["request"],
        "model_api_version": MODEL_API_VERSION,
        "model": model,
        "messages": msgs,
        **{k: v for k, v in fields.items() if v is not None},
    }


def required_capabilities(req: Mapping[str, Any]) -> list[str]:
    """Capabilities a route must have to serve this request: the explicit ones plus those the request implies."""
    caps = set(req.get("capabilities") or [])
    if req.get("stream"):
        caps.add("streaming")
    if req.get("tools") and req.get("tool_choice", "auto") != "none":
        caps.add("tools")
    if (req.get("response_format") or {}).get("type") == "json_schema":
        caps.add("structured_output")
    return [c for c in CAPABILITIES if c in caps]


def check_request(doc: Any) -> dict[str, Any]:
    """Schema plus semantic checks. Raises ModelApiError(INVALID_REQUEST | UNKNOWN_MODEL)."""
    if isinstance(doc, Mapping) and doc.get("contract") == KINDS["request"] and doc.get("model") not in LOGICAL_MODELS:
        raise ModelApiError(
            "UNKNOWN_MODEL",
            f"Unknown logical model {str(doc.get('model'))[:64]!r}.",
            details={"models": list(LOGICAL_MODELS)},
        )
    req = validate("request", doc, error_code="INVALID_REQUEST")
    tools = {t["name"] for t in req.get("tools") or []}
    if len(tools) != len(req.get("tools") or []):
        raise ModelApiError("INVALID_REQUEST", "Tool names must be unique.")
    choice = req.get("tool_choice")
    if isinstance(choice, dict) and choice["name"] not in tools:
        raise ModelApiError("INVALID_REQUEST", f"tool_choice names an undeclared tool {choice['name']!r}.")
    if choice == "required" and not tools:
        raise ModelApiError("INVALID_REQUEST", "tool_choice 'required' needs at least one tool.")
    for tool in req.get("tools") or []:
        _check_schema(tool["input_schema"], f"tool {tool['name']} input_schema")
    fmt = req.get("response_format") or {}
    if fmt.get("type") == "json_schema":
        _check_schema(fmt["schema"], "response_format.schema")
    seen_calls: set[str] = set()
    for i, msg in enumerate(req["messages"]):
        if msg.get("tool_calls") and msg["role"] != "assistant":
            raise ModelApiError("INVALID_REQUEST", f"messages[{i}]: only assistant messages carry tool_calls.")
        for call in msg.get("tool_calls") or []:
            seen_calls.add(call["id"])
        if msg["role"] == "tool":
            if msg.get("tool_call_id") not in seen_calls:
                raise ModelApiError("INVALID_REQUEST", f"messages[{i}]: tool result for an unknown tool_call_id.")
        elif "tool_call_id" in msg:
            raise ModelApiError("INVALID_REQUEST", f"messages[{i}]: tool_call_id belongs on tool messages.")
    if req["messages"][-1]["role"] == "assistant" and not req["messages"][-1].get("tool_calls"):
        raise ModelApiError("INVALID_REQUEST", "The last message must be from the user or a tool.")
    return req


def _check_schema(schema: dict[str, Any], where: str) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ModelApiError("INVALID_REQUEST", f"{where} is not a valid JSON Schema: {exc.message[:200]}") from None


def structured_errors(value: Any, schema: dict[str, Any]) -> list[str]:
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: list(map(str, e.absolute_path)))
    return [f"$/{'/'.join(map(str, e.absolute_path))}: {e.message[:160]}" for e in errors[:10]]


def new_response_id() -> str:
    return "mresp_" + ids.ulid()


# ---------------------------------------------------------------------------------------------- receipts

SAFE_FIELDS = ("model", "status", "finish_reason")


def safe_metadata(response: Mapping[str, Any]) -> dict[str, Any]:
    """The part of a response that may be recorded next to a receipt: no prompts, no output, no secrets."""
    usage = response["usage"]
    record = {
        "contract": "blux.model-call",
        "model_api_version": MODEL_API_VERSION,
        "response_id": response["response_id"],
        **{k: response[k] for k in SAFE_FIELDS},
        "provider": dict(response["provider"]),
        "routing": {
            "route_id": response["routing"]["route_id"],
            "fallback_used": response["routing"]["fallback_used"],
            "degraded": response["routing"]["degraded"],
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
                for a in response["routing"]["attempts"]
            ],
        },
        "usage": dict(usage),
        "determinism": dict(response["determinism"]),
        "timing": dict(response["timing"]),
        "output_shape": {
            "text_chars": len(response["output"]["text"] or ""),
            "json": response["output"]["json"] is not None,
            "tool_calls": [c["name"] for c in response["output"]["tool_calls"]],
        },
    }
    return scrub_doc(record)  # type: ignore[no-any-return]


def metadata_labels(record: Mapping[str, Any]) -> dict[str, str]:
    """A flat, bounded label map of the safe metadata (Contract v1 labels: <=64 keys, <=256 chars)."""
    usage = record["usage"]
    labels = {
        "model": record["model"],
        "status": record["status"],
        "finish_reason": record["finish_reason"],
        "provider": record["provider"]["provider"],
        "provider_model": record["provider"]["provider_model"],
        "backend": record["provider"]["backend"],
        "route_id": record["provider"]["route_id"],
        "locality": record["provider"]["locality"],
        "fallback_used": str(record["routing"]["fallback_used"]).lower(),
        "degraded": str(record["routing"]["degraded"]).lower(),
        "attempts": str(len(record["routing"]["attempts"])),
        "latency_ms": str(record["timing"]["latency_ms"]),
        "deterministic_applied": str(record["determinism"]["supported"] and record["determinism"]["requested"]).lower(),
    }
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        if usage.get(key) is not None:
            labels[key] = str(usage[key])
    return {k: scrub(v)[:256] for k, v in labels.items()}
