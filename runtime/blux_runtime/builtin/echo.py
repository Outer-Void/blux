"""echo.test: reference capability proving a full Contract v1 round trip. Pure, deterministic, no side effects."""

from __future__ import annotations

from typing import Any

from blux_runtime.interfaces import ExecutionContext

DESCRIPTOR: dict[str, Any] = {
    "contract": "blux.capability",
    "contract_version": "1.0",
    "capability_id": "echo.test",
    "version": "1.0.0",
    "summary": "Return the given message, optionally upper-cased, with its length.",
    "contract_versions": ["1.0"],
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["message"],
        "properties": {
            "message": {"type": "string", "maxLength": 4096},
            "uppercase": {"type": "boolean"},
        },
    },
    "output_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["message", "length", "uppercase"],
        "properties": {
            "message": {"type": "string"},
            "length": {"type": "integer", "minimum": 0},
            "uppercase": {"type": "boolean"},
        },
    },
    "side_effects": "none",
    "required_permissions": [],
    "idempotent": True,
    "timeout_ms": 1000,
    "provider": {"repository": "Outer-Void/blux", "kind": "builtin", "entrypoint": "blux_runtime.builtin.echo:handle"},
    "labels": {"stage": "reference"},
}


def handle(input: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:  # noqa: A002
    upper = bool(input.get("uppercase", False))
    message = input["message"].upper() if upper else input["message"]
    return {"message": message, "length": len(message), "uppercase": upper}
