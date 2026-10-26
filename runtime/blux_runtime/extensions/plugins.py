"""Plugins: out-of-process capabilities and hook subscribers (Stage 14; parity matrix BLH-016).

BLHermes plugins load in process and can read the agent's credentials. A BLUX plugin is a directory with
`blux-plugin.json`:

    {"contract": "blux.plugin", "plugin_id": "acme-audit", "version": "1.0.0", "license": "MIT",
     "capabilities": [<blux.capability descriptors with provider.kind = "subprocess">],
     "hooks": {"events": [...], "permissions": [...], "command": "acme-audit --hook"}}

- **Capabilities.** They run through the subprocess provider (ADR-0009): their own process, a minimal
  environment, and the timeout. Policy decides who may call them.
- **Hooks.** They go through `ExternalSubscribers` (ADR-0012): the operator's HookPolicy grants, a
  credential-free subprocess, and no access to the bus.
- **Load-time checks.** A plugin without a licence, with an unknown capability provider kind, or whose
  capability ids collide with existing ones is rejected. Nothing is executed at load time.

Clean-room implementation; no upstream code.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from blux_runtime.hooks.adapters import ExternalSubscribers, SubprocessTransport, plugin_manifest
from blux_runtime.hooks.bus import HookBus, HookError
from blux_runtime.registry import CapabilityRegistry
from blux_runtime.subprocess_provider import SubprocessProvider
from blux_sdk.capability import CapabilityDescriptor
from blux_sdk.errors import BluxError

PLUGIN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class PluginError(ValueError):
    pass


@dataclass
class LoadedPlugin:
    plugin_id: str
    version: str
    license: str
    path: Path
    capabilities: list[str] = field(default_factory=list)
    hook_id: str | None = None


def load(path: Path, registry: CapabilityRegistry, *, hooks: HookBus | None = None) -> LoadedPlugin:
    manifest_file = Path(path) / "blux-plugin.json"
    try:
        doc = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PluginError(f"{manifest_file}: unreadable ({type(exc).__name__})") from None
    if doc.get("contract") != "blux.plugin":
        raise PluginError(f"{manifest_file}: not a blux.plugin manifest")
    pid = str(doc.get("plugin_id", ""))
    if not PLUGIN_ID.match(pid):
        raise PluginError(f"{manifest_file}: invalid plugin_id {pid!r}")
    if not doc.get("license"):
        raise PluginError(f"{pid}: no license declared")
    descriptors = []
    for raw in doc.get("capabilities") or []:
        try:
            d = CapabilityDescriptor.from_document(raw)
        except BluxError as exc:
            raise PluginError(f"{pid}: invalid capability descriptor: {exc.message}") from None
        if d.provider.get("kind") != "subprocess":
            raise PluginError(f"{pid}: {d.capability_id} must use provider.kind = subprocess (no in-process plugins)")
        if not d.capability_id.startswith(f"{pid}.") and not d.capability_id.startswith(f"plugin.{pid}."):
            raise PluginError(f"{pid}: capability {d.capability_id} must be namespaced under {pid}.")
        if d.capability_id in registry:
            raise PluginError(f"{pid}: capability {d.capability_id} already exists")
        descriptors.append(d)
    loaded = LoadedPlugin(pid, str(doc.get("version", "0")), str(doc["license"]), Path(path))
    for d in descriptors:
        registry.register(d, SubprocessProvider(d, cwd=str(path)))
        loaded.capabilities.append(d.capability_id)
    hook = doc.get("hooks")
    if hook:
        if hooks is None:
            raise PluginError(f"{pid}: declares hooks but no hook bus is configured")
        manifest = plugin_manifest(
            pid,
            list(hook["events"]),
            list(hook.get("permissions") or []),
            timeout_ms=int(hook.get("timeout_ms", 1000)),
            version=loaded.version,
        )
        try:
            loaded.hook_id = ExternalSubscribers(hooks).register(manifest, SubprocessTransport(str(hook["command"])))
        except HookError as exc:
            raise PluginError(f"{pid}: hook registration refused: {exc}") from None
    return loaded
