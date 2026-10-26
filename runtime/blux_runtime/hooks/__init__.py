"""BLUX Hook System v1 (ADR-0012): typed, versioned events on a bus BLUX owns."""

from blux_runtime.hooks.adapters import (
    CallableTransport,
    ExternalSubscribers,
    SubprocessTransport,
    mcp_manifest,
    plugin_manifest,
    skill_manifest,
)
from blux_runtime.hooks.bus import (
    Emission,
    EventContext,
    HookBus,
    HookError,
    HookEvent,
    HookPermissionError,
    HookPolicy,
    HookResult,
    HookSource,
    Registration,
)
from blux_runtime.hooks.catalog import HOOKS_VERSION, EventSpec, spec
from blux_runtime.hooks.catalog import catalog as event_catalog

__all__ = [
    "HOOKS_VERSION",
    "CallableTransport",
    "Emission",
    "EventContext",
    "EventSpec",
    "ExternalSubscribers",
    "HookBus",
    "HookError",
    "HookEvent",
    "HookPermissionError",
    "HookPolicy",
    "HookResult",
    "HookSource",
    "Registration",
    "SubprocessTransport",
    "event_catalog",
    "mcp_manifest",
    "plugin_manifest",
    "skill_manifest",
    "spec",
]
