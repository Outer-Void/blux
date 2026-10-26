"""Adapters for external hook subscribers: plugins, skills and MCP servers (ADR-0012).

An external subscriber never touches the bus. It sends a `blux.hook-registration` manifest, and BLUX's hook
policy decides what to grant. Then it receives events through a transport and answers with a small JSON
verdict. The bus keeps ownership of everything else: ordering, timeouts, permissions, redaction, receipts
and quarantine.

  verdict  {"action": "ok"} | {"action": "veto", "reason": "..."} | {"action": "mutate", "fields": {...}}

Anything else, including `"allow"`, is a protocol violation. External subscribers cannot emit events, and
their priority is capped at +/-100, so BLUX's own guards always run first.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from blux_runtime.hooks import catalog
from blux_runtime.hooks.bus import HookBus, HookError, HookEvent, HookResult, HookSource, Registration
from blux_runtime.subprocess_provider import minimal_env

MAX_VERDICT_BYTES = 64 * 1024


class Transport(Protocol):
    def deliver(self, event: dict[str, Any], *, timeout_s: float) -> Mapping[str, Any] | None:
        """Send one event document; return the subscriber's verdict (or None for ok)."""
        ...


class CallableTransport:
    """In-process transport for tests and embedding: fn(event_document) -> verdict."""

    def __init__(self, fn: Callable[[dict[str, Any]], Mapping[str, Any] | None]) -> None:
        self.fn = fn

    def deliver(self, event: dict[str, Any], *, timeout_s: float) -> Mapping[str, Any] | None:
        return self.fn(json.loads(json.dumps(event)))  # the subscriber only ever sees a copy


class SubprocessTransport:
    """One process per event: the event as JSON on stdin, the verdict as one JSON object on stdout.

    The child gets a minimal environment (no credentials) and is killed at the deadline.
    """

    def __init__(self, command: str, *, env: Mapping[str, str] | None = None) -> None:
        self.argv = shlex.split(command)
        if not self.argv:
            raise HookError("empty subscriber command")
        self.env = minimal_env(dict(env) if env is not None else None)

    def deliver(self, event: dict[str, Any], *, timeout_s: float) -> Mapping[str, Any] | None:
        exe = shutil.which(self.argv[0], path=self.env.get("PATH"))
        if exe is None:
            raise HookError(f"subscriber executable {self.argv[0]!r} not found")
        proc = subprocess.run(  # noqa: S603 - argv from an operator-approved registration, no shell
            [exe, *self.argv[1:]],
            input=json.dumps(event).encode("utf-8"),
            capture_output=True,
            timeout=timeout_s,
            env=self.env,
            check=False,
        )
        if proc.returncode != 0:
            raise HookError(f"subscriber exited {proc.returncode}")
        out = proc.stdout.strip()
        if not out:
            return None
        if len(out) > MAX_VERDICT_BYTES:
            raise HookError("subscriber verdict too large")
        doc = json.loads(out)
        if not isinstance(doc, dict):
            raise HookError("subscriber verdict must be a JSON object")
        return doc


def verdict_to_result(verdict: Mapping[str, Any] | None) -> Any:
    """Translate an external verdict. Unknown actions pass through as a non-HookResult so the bus rejects them."""
    if verdict is None:
        return HookResult.ok()
    action = verdict.get("action", "ok")
    if action == "ok":
        return HookResult.ok()
    if action == "veto":
        return HookResult.veto(str(verdict.get("reason") or "vetoed by subscriber"))
    if action == "mutate" and isinstance(verdict.get("fields"), Mapping):
        return HookResult.mutate(**dict(verdict["fields"]))
    return {"rejected_verdict": str(action)[:32]}


class ExternalSubscribers:
    """The only door into the bus for plugins, skills and MCP servers."""

    def __init__(self, bus: HookBus) -> None:
        self._bus = bus

    def register(self, manifest: Mapping[str, Any], transport: Transport) -> str:
        errors = catalog.schema_errors("registration", manifest)
        if errors:
            raise HookError(f"invalid hook registration: {errors[0]}")
        src = manifest["source"]
        source = HookSource(src["kind"], src["id"], src.get("version"), src.get("digest"))
        timeout_ms = int(manifest.get("timeout_ms", 1000))

        def handler(event: HookEvent) -> Any:
            return verdict_to_result(transport.deliver(event.to_document(), timeout_s=timeout_ms / 1000))

        reg: Registration = self._bus.register(
            manifest["events"],
            handler,
            source=source,
            permissions=manifest["permissions"],
            hook_id=manifest["hook_id"],
            priority=int(manifest.get("priority", 0)),
            timeout_ms=timeout_ms,
            failure_policy=manifest.get("failure_policy", "ignore"),
        )
        return reg.hook_id

    def unregister(self, hook_id: str) -> bool:
        """Remove an external subscriber (never a BLUX-owned hook)."""
        reg = next((r for r in self._bus.registrations() if r.hook_id == hook_id), None)
        if reg is None or reg.source.kind not in ("plugin", "skill", "mcp"):
            return False
        return self._bus.unregister(hook_id)


def plugin_manifest(
    plugin_id: str, events: list[str], permissions: list[str] | None = None, **extra: Any
) -> dict[str, Any]:
    return _manifest("plugin", plugin_id, events, permissions, extra)


def skill_manifest(
    skill_id: str, events: list[str], permissions: list[str] | None = None, **extra: Any
) -> dict[str, Any]:
    return _manifest("skill", skill_id, events, permissions, extra)


def mcp_manifest(
    server_id: str, events: list[str], permissions: list[str] | None = None, **extra: Any
) -> dict[str, Any]:
    return _manifest("mcp", server_id, events, permissions, extra)


def _manifest(
    kind: str, sid: str, events: list[str], permissions: list[str] | None, extra: dict[str, Any]
) -> dict[str, Any]:
    return {
        "contract": "blux.hook-registration",
        "hooks_version": catalog.HOOKS_VERSION,
        "hook_id": extra.pop("hook_id", f"{kind}.{sid}"),
        "source": {"kind": kind, "id": sid, **({"version": extra.pop("version")} if "version" in extra else {})},
        "events": events,
        "permissions": permissions or [],
        **extra,
    }
