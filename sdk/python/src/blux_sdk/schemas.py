"""Packaged Contract v1 schemas and envelope validation.

The schemas under blux_sdk/schemas/v1 are a byte-identical copy of the canonical contracts/v1 tree
(packaging/sync_schemas.py writes it; tests and CI fail on drift). They are loaded with
importlib.resources so an installed wheel validates exactly like a source checkout.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Mapping
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource

from blux_sdk.errors import BluxError, ErrorCode
from blux_sdk.timeutil import parse_timestamp
from blux_sdk.versions import SUPPORTED_CONTRACT_VERSIONS, parse_version, require_supported

URN_PREFIX = "urn:blux:contract:v1:"

# kind -> value of the envelope's "contract" field
KINDS: dict[str, str] = {
    "capability": "blux.capability",
    "capability-set": "blux.capability-set",
    "execution": "blux.execution",
    "result": "blux.result",
    "receipt": "blux.receipt",
    "artifact": "blux.artifact",
    "model": "blux.model",
    "project": "blux.project",
    "policy": "blux.policy",
    "error": "blux.error",
}
CONTRACT_TO_KIND = {v: k for k, v in KINDS.items()}

_MAX_REPORTED_ERRORS = 20

_TIMESTAMP_FIELDS: dict[str, tuple[str, ...]] = {
    "execution": ("requested_at",),
    "result": ("started_at", "finished_at"),
    "receipt": ("started_at", "finished_at"),
    "artifact": ("created_at",),
}


def _schema_files() -> dict[str, dict[str, Any]]:
    root = resources.files("blux_sdk").joinpath("schemas").joinpath("v1")
    out: dict[str, dict[str, Any]] = {}
    for entry in root.iterdir():
        if entry.name.endswith(".schema.json"):
            out[entry.name.removesuffix(".schema.json")] = json.loads(entry.read_text(encoding="utf-8"))
    return out


@functools.lru_cache(maxsize=1)
def load_schemas() -> dict[str, dict[str, Any]]:
    schemas = _schema_files()
    missing = (set(KINDS) | {"common"}) - set(schemas)
    if missing:
        raise RuntimeError(f"blux_sdk is missing packaged schemas: {sorted(missing)}")
    return schemas


@functools.lru_cache(maxsize=1)
def registry() -> Registry:
    reg: Registry = Registry()
    for schema in load_schemas().values():
        reg = reg.with_resource(schema["$id"], Resource.from_contents(schema))
    return reg


@functools.cache
def validator(kind: str) -> Draft202012Validator:
    if kind not in KINDS:
        raise ValueError(f"unknown contract kind: {kind}")
    return Draft202012Validator(load_schemas()[kind], registry=registry())


def schema_errors(kind: str, document: Any) -> list[dict[str, Any]]:
    """Schema violations as JSON-pointer-ish paths and failing keywords (never instance values)."""
    errors = sorted(validator(kind).iter_errors(document), key=lambda e: (list(e.absolute_path), e.validator))
    return [
        {
            "path": "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in e.absolute_path),
            "keyword": str(e.validator),
        }
        for e in errors[:_MAX_REPORTED_ERRORS]
    ]


def validate(kind: str, document: Any) -> None:
    """Raise SCHEMA_VALIDATION_FAILED if document does not satisfy the v1 schema for kind."""
    errors = schema_errors(kind, document)
    if errors:
        raise BluxError(
            ErrorCode.SCHEMA_VALIDATION_FAILED,
            f"Document does not satisfy the blux.{kind} v1 schema.",
            details={"kind": kind, "errors": errors},
        )


def check_envelope(
    document: Any,
    kind: str,
    *,
    supported: tuple[str, ...] = SUPPORTED_CONTRACT_VERSIONS,
) -> dict[str, Any]:
    """Full gate for an incoming contract document.

    Order matters so each failure class gets its own explicit code:
    object -> contract kind -> version syntax -> version support -> schema -> semantic checks.
    """
    if not isinstance(document, Mapping):
        raise BluxError(
            ErrorCode.MALFORMED_INPUT,
            "Malformed input: not_an_object.",
            details={"reason": "not_an_object", "type": type(document).__name__},
        )
    expected = KINDS[kind]
    if document.get("contract") != expected:
        raise BluxError(
            ErrorCode.INVALID_ENVELOPE,
            f"Expected a {expected} document.",
            details={"expected": expected, "received": _short(document.get("contract"))},
        )
    if "contract_version" not in document:
        raise BluxError(
            ErrorCode.INVALID_ENVELOPE, "contract_version is required.", details={"field": "contract_version"}
        )
    parse_version(document["contract_version"])
    require_supported(document["contract_version"], supported)
    doc = dict(document)
    validate(kind, doc)
    _semantic_checks(kind, doc)
    return doc


def check_json_schema(schema: Any, *, field: str) -> None:
    """Validate that schema is itself a usable JSON Schema 2020-12 document."""
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise BluxError(
            ErrorCode.SCHEMA_VALIDATION_FAILED,
            f"{field} is not a valid JSON Schema.",
            details={"field": field, "keyword": str(exc.validator)},
        ) from None


def _semantic_checks(kind: str, doc: dict[str, Any]) -> None:
    if kind == "model" and not doc["model_id"].startswith(doc["provider"] + "/"):
        raise BluxError(
            ErrorCode.SCHEMA_VALIDATION_FAILED,
            "model_id must start with '<provider>/'.",
            details={"kind": kind, "errors": [{"path": "$.model_id", "keyword": "provider_prefix"}]},
        )
    if kind == "capability":
        check_json_schema(doc["input_schema"], field="input_schema")
        check_json_schema(doc["output_schema"], field="output_schema")
        for v in doc["contract_versions"]:
            parse_version(v)
    if kind == "capability-set":
        seen: set[str] = set()
        for i, cap in enumerate(doc["capabilities"]):
            _semantic_checks("capability", cap)
            if cap["capability_id"] in seen:
                raise BluxError(
                    ErrorCode.SCHEMA_VALIDATION_FAILED,
                    "Duplicate capability_id in capability set.",
                    details={
                        "kind": kind,
                        "errors": [{"path": f"$.capabilities[{i}].capability_id", "keyword": "unique"}],
                    },
                )
            seen.add(cap["capability_id"])
    if kind == "policy":
        seen_rules: set[str] = set()
        for i, rule in enumerate(doc["rules"]):
            if rule["rule_id"] in seen_rules:
                raise BluxError(
                    ErrorCode.SCHEMA_VALIDATION_FAILED,
                    "Duplicate rule_id in policy.",
                    details={"kind": kind, "errors": [{"path": f"$.rules[{i}].rule_id", "keyword": "unique"}]},
                )
            seen_rules.add(rule["rule_id"])
    parsed = {}
    for field in _TIMESTAMP_FIELDS.get(kind, ()):
        if field in doc:
            try:
                parsed[field] = parse_timestamp(doc[field])
            except ValueError:
                raise BluxError(
                    ErrorCode.SCHEMA_VALIDATION_FAILED,
                    f"{field} is not a real calendar date/time.",
                    details={"kind": kind, "errors": [{"path": f"$.{field}", "keyword": "calendar"}]},
                ) from None
    if kind in ("result", "receipt") and parsed["finished_at"] < parsed["started_at"]:
        raise BluxError(
            ErrorCode.SCHEMA_VALIDATION_FAILED,
            "finished_at precedes started_at.",
            details={"kind": kind, "errors": [{"path": "$.finished_at", "keyword": "ordering"}]},
        )


def _short(value: Any) -> Any:
    if isinstance(value, str):
        return value[:64]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"<{type(value).__name__}>"
