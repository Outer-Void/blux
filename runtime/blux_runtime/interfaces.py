"""Runtime-facing interfaces. Implementations live in this repo (reference) or in integrated repos."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ExecutionContext:
    """What a handler may know about the operation. Deliberately excludes policy and credentials."""

    operation_id: str
    parent_operation_id: str | None
    session_id: str | None
    project_id: str | None
    actor: dict[str, str]
    capability_id: str
    timeout_ms: int


class CapabilityHandler(Protocol):
    def __call__(self, input: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:  # noqa: A002
        ...


class ReceiptSink(Protocol):
    """Where receipts go. BLUX emits; durable storage is blux-system's authority (ADR-0002)."""

    def emit(self, receipt: dict[str, Any]) -> None: ...


class ModelProvider(Protocol):
    """A model a runtime can route to. Invocation happens through capabilities, not this interface."""

    def describe(self) -> dict[str, Any]:
        """Return a blux.model document."""
        ...
