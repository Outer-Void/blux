"""MCP client: tools of a stdio MCP server as BLUX capabilities (Stage 14; parity matrix BLH-017).

Each configured server is a separate process speaking JSON-RPC 2.0 over stdio (newline-delimited):
`initialize`, then `notifications/initialized`, then `tools/list` and `tools/call`. Every server tool
becomes the capability `mcp.<server>.<tool>`:

- `labels.kind = tool`, with `side_effects` set by the operator's server configuration.
- `required_permissions: [mcp:<server>]`, so policy must grant the server explicitly.

Child process rules:
- **Environment.** The allow-list environment plus only the variables the configuration names in
  `credential_env` (announced as `credential.requested`, names only).
- **Limits.** Each call has its own timeout. A hung or dead server is killed and restarted on the next
  call. Output is redacted.

Clean-room implementation; no upstream code.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from blux_runtime.hooks.bus import EventContext, HookBus, HookError
from blux_runtime.interfaces import ExecutionContext
from blux_runtime.registry import CapabilityRegistry
from blux_runtime.security import child_env, redact
from blux_sdk.errors import BluxError, ErrorCode

PROTOCOL_VERSION = "2025-06-18"
SERVER_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
TOOL_SEGMENT = re.compile(r"[^a-z0-9_-]")
MAX_LINE = 4 * 1024 * 1024
SIDE_EFFECTS = ("none", "read", "write", "external", "destructive")


class McpError(RuntimeError):
    pass


@dataclass
class ServerConfig:
    server_id: str
    command: str
    side_effects: str = "external"
    credential_env: list[str] = field(default_factory=list)
    timeout_ms: int = 30000

    def __post_init__(self) -> None:
        if not SERVER_ID.match(self.server_id):
            raise McpError(f"invalid MCP server id {self.server_id!r}")
        if self.side_effects not in SIDE_EFFECTS:
            raise McpError(f"invalid side_effects {self.side_effects!r}")


class McpServer:
    def __init__(
        self, config: ServerConfig, *, environ: Mapping[str, str] | None = None, hooks: HookBus | None = None
    ) -> None:
        self.config = config
        self.environ = dict(os.environ if environ is None else environ)
        self.hooks = hooks
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._next_id = 0

    # process ----------------------------------------------------------------------------------------------

    def _env(self) -> dict[str, str]:
        extra = {k: self.environ[k] for k in self.config.credential_env if k in self.environ}
        if self.config.credential_env and self.hooks is not None:
            with contextlib.suppress(HookError):
                self.hooks.emit(
                    "credential.requested",
                    {
                        "names": list(self.config.credential_env),
                        "purpose": f"mcp server {self.config.server_id}",
                        "route_id": None,
                    },
                    context=EventContext(),
                )
        return child_env(self.environ, extra=extra)

    def _start(self) -> subprocess.Popen[bytes]:
        argv = shlex.split(self.config.command)
        env = self._env()
        exe = shutil.which(argv[0], path=env.get("PATH")) if argv else None
        if exe is None:
            raise McpError(f"MCP server executable not found for {self.config.server_id}")
        proc = subprocess.Popen(  # noqa: S603 - argv from operator configuration, no shell
            [exe, *argv[1:]],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
        self._proc = proc
        self._request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "blux", "version": "1"}},
            10,
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return proc

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    def _send(self, msg: dict[str, Any]) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self._proc.stdin.flush()

    def _request(self, method: str, params: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        assert self._proc is not None and self._proc.stdout is not None
        self._next_id += 1
        rid = self._next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        box: dict[str, Any] = {}

        def read() -> None:
            assert self._proc is not None and self._proc.stdout is not None
            while True:
                line = self._proc.stdout.readline(MAX_LINE)
                if not line:
                    box["eof"] = True
                    return
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("id") == rid:
                    box["msg"] = msg
                    return

        t = threading.Thread(target=read, daemon=True)
        t.start()
        t.join(timeout_s)
        if t.is_alive() or "eof" in box:
            self.close()
            raise McpError("timeout" if t.is_alive() else "server exited")
        msg = box["msg"]
        if "error" in msg:
            raise McpError(f"server error {msg['error'].get('code')}: {str(msg['error'].get('message'))[:200]}")
        result = msg.get("result")
        if not isinstance(result, dict):
            raise McpError("malformed result")
        return result

    def _ensure(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            self._start()

    # api ---------------------------------------------------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure()
            tools = self._request("tools/list", {}, self.config.timeout_ms / 1000).get("tools") or []
        return [t for t in tools if isinstance(t, dict) and isinstance(t.get("name"), str)]

    def call(self, name: str, arguments: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        with self._lock:
            self._ensure()
            return self._request("tools/call", {"name": name, "arguments": arguments}, timeout_s)


def capability_id(server: str, tool: str) -> str:
    return f"mcp.{server}.{TOOL_SEGMENT.sub('_', tool.lower())[:64]}"


def register(registry: CapabilityRegistry, server: McpServer) -> list[str]:
    """Discover the server's tools and register one capability each. Returns the capability ids."""
    cfg = server.config
    ids: list[str] = []
    for tool in server.list_tools():
        cid = capability_id(cfg.server_id, tool["name"])
        schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {"type": "object"}
        descriptor = {
            "contract": "blux.capability",
            "contract_version": "1.0",
            "capability_id": cid,
            "version": "1.0.0",
            "summary": str(tool.get("description") or tool["name"])[:256],
            "contract_versions": ["1.0"],
            "input_schema": schema,
            "output_schema": {"type": "object"},
            "side_effects": cfg.side_effects,
            "required_permissions": [f"mcp:{cfg.server_id}"],
            "idempotent": False,
            "timeout_ms": cfg.timeout_ms,
            "provider": {"repository": "Outer-Void/blux", "kind": "builtin", "entrypoint": f"mcp:{cfg.server_id}"},
            "labels": {"kind": "tool", "mcp_server": cfg.server_id},
        }

        def handler(input: dict[str, Any], context: ExecutionContext, _name: str = tool["name"]) -> dict[str, Any]:  # noqa: A002
            t0 = time.monotonic()
            try:
                result = server.call(_name, input, context.timeout_ms / 1000)
            except McpError as exc:
                raise BluxError(
                    ErrorCode.EXECUTION_FAILED, f"MCP call failed: {exc}", details={"reason": "mcp_error"}
                ) from None
            text = "".join(
                str(c.get("text", ""))
                for c in result.get("content") or []
                if isinstance(c, dict) and c.get("type") == "text"
            )
            out: dict[str, Any] = {
                "text": redact(text),
                "is_error": bool(result.get("isError")),
                "latency_ms": int((time.monotonic() - t0) * 1000),
            }
            if result.get("isError"):
                raise BluxError(
                    ErrorCode.EXECUTION_FAILED,
                    "MCP tool reported an error.",
                    details={"reason": "tool_error", "text": out["text"][:512]},
                )
            return out

        registry.register(descriptor, handler)
        ids.append(cid)
    return ids
