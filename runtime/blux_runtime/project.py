"""Register a repository's `.blux/` capabilities with a runtime (docs/spec/dot-blux.md, ADR-0009).

Loading stays inert: descriptors are validated and handlers are built, but nothing is executed until a
request for that capability passes policy.
"""

from __future__ import annotations

from typing import Any

from blux_runtime.interfaces import ExecutionContext
from blux_runtime.registry import CapabilityRegistry
from blux_runtime.subprocess_provider import SubprocessProvider
from blux_sdk.capability import CapabilityDescriptor
from blux_sdk.dot_blux import DotBlux
from blux_sdk.errors import BluxError, ErrorCode


def _unsupported(kind: str) -> Any:
    def handler(_input: dict[str, Any], _context: ExecutionContext) -> dict[str, Any]:
        raise BluxError(
            ErrorCode.EXECUTION_FAILED,
            f"Provider kind {kind!r} is not supported by this runtime.",
            details={"reason": "unsupported_provider_kind", "kind": kind},
        )

    return handler


def register_project(registry: CapabilityRegistry, project: DotBlux, *, cwd: str | None = None) -> list[str]:
    """Add every capability of `project` to `registry`; returns their ids. Duplicate ids are an error."""
    added = []
    for descriptor in project.capabilities():
        kind = descriptor.provider["kind"]
        handler = SubprocessProvider(descriptor, cwd=cwd) if kind == "subprocess" else _unsupported(kind)
        registry.register(descriptor, handler)
        added.append(descriptor.capability_id)
    return added


__all__ = ["CapabilityDescriptor", "register_project"]
