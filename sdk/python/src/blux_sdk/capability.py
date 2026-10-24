"""Capability metadata (ADR-0003)."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

from blux_sdk.errors import BluxError, ErrorCode
from blux_sdk.schemas import check_envelope, registry
from blux_sdk.versions import CONTRACT_VERSION

SIDE_EFFECT_ORDER = ("none", "read", "write", "external", "destructive")


def side_effect_rank(value: str) -> int:
    return SIDE_EFFECT_ORDER.index(value)


@dataclass(frozen=True)
class CapabilityDescriptor:
    capability_id: str
    version: str
    summary: str
    contract_versions: tuple[str, ...]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    side_effects: str
    required_permissions: tuple[str, ...]
    idempotent: bool
    timeout_ms: int
    provider: dict[str, str]
    labels: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_document(cls, document: Any) -> CapabilityDescriptor:
        doc = check_envelope(document, "capability")
        return cls(
            capability_id=doc["capability_id"],
            version=doc["version"],
            summary=doc["summary"],
            contract_versions=tuple(doc["contract_versions"]),
            input_schema=copy.deepcopy(doc["input_schema"]),
            output_schema=copy.deepcopy(doc["output_schema"]),
            side_effects=doc["side_effects"],
            required_permissions=tuple(doc["required_permissions"]),
            idempotent=doc["idempotent"],
            timeout_ms=doc["timeout_ms"],
            provider=dict(doc["provider"]),
            labels=dict(doc.get("labels", {})),
        )

    def to_document(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "contract": "blux.capability",
            "contract_version": CONTRACT_VERSION,
            "capability_id": self.capability_id,
            "version": self.version,
            "summary": self.summary,
            "contract_versions": list(self.contract_versions),
            "input_schema": copy.deepcopy(self.input_schema),
            "output_schema": copy.deepcopy(self.output_schema),
            "side_effects": self.side_effects,
            "required_permissions": list(self.required_permissions),
            "idempotent": self.idempotent,
            "timeout_ms": self.timeout_ms,
            "provider": dict(self.provider),
        }
        if self.labels:
            doc["labels"] = dict(self.labels)
        return doc

    def validate_input(self, value: Any) -> None:
        _validate_payload(self.input_schema, value, ErrorCode.SCHEMA_VALIDATION_FAILED, "input")

    def validate_output(self, value: Any) -> None:
        _validate_payload(self.output_schema, value, ErrorCode.OUTPUT_VALIDATION_FAILED, "output")


def _validate_payload(schema: dict[str, Any], value: Any, code: ErrorCode, field_name: str) -> None:
    v = Draft202012Validator(schema, registry=registry())
    errors = sorted(v.iter_errors(value), key=lambda e: list(e.absolute_path))
    if errors:
        raise BluxError(
            code,
            f"Capability {field_name} does not satisfy its declared schema.",
            details={
                "field": field_name,
                "errors": [
                    {
                        "path": "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in e.absolute_path),
                        "keyword": str(e.validator),
                    }
                    for e in errors[:20]
                ],
            },
        )
