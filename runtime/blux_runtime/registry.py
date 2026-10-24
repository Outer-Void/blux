"""In-process capability and model registries."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from blux_runtime.interfaces import CapabilityHandler, ModelProvider
from blux_sdk.capability import CapabilityDescriptor
from blux_sdk.errors import BluxError, ErrorCode
from blux_sdk.schemas import check_envelope
from blux_sdk.versions import CONTRACT_VERSION


@dataclass(frozen=True)
class RegisteredCapability:
    descriptor: CapabilityDescriptor
    handler: CapabilityHandler


class CapabilityRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, RegisteredCapability] = {}

    def register(self, descriptor: CapabilityDescriptor | dict[str, Any], handler: CapabilityHandler) -> None:
        if not isinstance(descriptor, CapabilityDescriptor):
            descriptor = CapabilityDescriptor.from_document(descriptor)
        else:
            CapabilityDescriptor.from_document(descriptor.to_document())  # re-validate
        if descriptor.capability_id in self._entries:
            raise ValueError(f"capability already registered: {descriptor.capability_id}")
        if not callable(handler):
            raise TypeError("handler must be callable")
        self._entries[descriptor.capability_id] = RegisteredCapability(descriptor, handler)

    def get(self, capability_id: str) -> RegisteredCapability:
        try:
            return self._entries[capability_id]
        except KeyError:
            raise BluxError(
                ErrorCode.UNKNOWN_CAPABILITY,
                f"Unknown capability {capability_id}.",
                details={"capability_id": capability_id},
            ) from None

    def __iter__(self) -> Iterator[RegisteredCapability]:
        return iter(sorted(self._entries.values(), key=lambda e: e.descriptor.capability_id))

    def __contains__(self, capability_id: object) -> bool:
        return capability_id in self._entries

    def to_capability_set(self) -> dict[str, Any]:
        return check_envelope(
            {
                "contract": "blux.capability-set",
                "contract_version": CONTRACT_VERSION,
                "capabilities": [e.descriptor.to_document() for e in self],
            },
            "capability-set",
        )


class ModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, tuple[dict[str, Any], ModelProvider]] = {}

    def register(self, provider: ModelProvider) -> str:
        doc = check_envelope(provider.describe(), "model")
        if doc["model_id"] in self._models:
            raise ValueError(f"model already registered: {doc['model_id']}")
        self._models[doc["model_id"]] = (doc, provider)
        return str(doc["model_id"])

    def describe(self, model_id: str) -> dict[str, Any]:
        return dict(self._models[model_id][0])

    def model_ids(self) -> list[str]:
        return sorted(self._models)
