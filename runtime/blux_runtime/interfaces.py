"""Runtime-facing interfaces. Implementations live in this repo (reference) or in integrated repos."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ExecutionContext:
    """What a handler may know about the operation. Deliberately excludes policy and credentials.

    Built-in handlers may also use three optional channels (subprocess providers never see them):
    - `attach_artifact(descriptor, data)`: describe an artifact produced by this operation; the executor
      validates it, lists it in the receipt's artifact_ids and passes it to the receipt sink;
    - `emit_event(event)`: progress events (Model API stream events) for a caller that asked for them;
    - `cancel`: set when the caller cancels; long-running handlers should stop promptly.
    """

    operation_id: str
    parent_operation_id: str | None
    session_id: str | None
    project_id: str | None
    actor: dict[str, str]
    capability_id: str
    timeout_ms: int
    artifacts: list[tuple[dict[str, Any], bytes]] = field(default_factory=list, compare=False, repr=False)
    on_event: Callable[[dict[str, Any]], None] | None = field(default=None, compare=False, repr=False)
    cancel: threading.Event = field(default_factory=threading.Event, compare=False, repr=False)

    def attach_artifact(self, descriptor: dict[str, Any], data: bytes) -> None:
        self.artifacts.append((descriptor, data))

    def emit_event(self, event: dict[str, Any]) -> None:
        if self.on_event is not None:
            self.on_event(event)


class CapabilityHandler(Protocol):
    def __call__(self, input: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:  # noqa: A002
        ...


class ReceiptSink(Protocol):
    """Where receipts go. BLUX emits; durable storage is blux-system's authority (ADR-0002).

    A sink may also define `emit_artifact(descriptor, data)`; the executor calls it, before the receipt, for
    each artifact a handler attached.
    """

    def emit(self, receipt: dict[str, Any]) -> None: ...


class ModelProvider(Protocol):
    """A model a runtime can route to. Invocation happens through capabilities, not this interface."""

    def describe(self) -> dict[str, Any]:
        """Return a blux.model document."""
        ...
