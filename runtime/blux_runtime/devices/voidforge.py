"""VoidForge device capabilities (Stage 22): `device.*` tools that ask a running VoidForge to act.

VoidForge is the authority for Android devices. It runs standalone, and BLUX is one optional client.
BLUX can only ask:

- every `device.*` capability is a tool labelled `device_operation`, so hooks cannot mutate it;
- requests never carry authorization (a request with grant/approval fields is refused here, and by
  VoidForge);
- VoidForge applies its own policy, write barrier and local authorization: the person at the VoidForge
  device confirms every write there, whatever BLUX policy, approvals or YOLO say;
- when VoidForge is not running, integration is off, or no device is connected, the call fails with
  `reason: voidforge_unavailable`, and the rest of BLUX keeps working.

The capability catalog is VoidForge's (`voidforge_capabilities.json`, copied from
Outer-Void/voidforge `integration/protocol/v1/capabilities.json`). The wire protocol is
`voidforge-integration/1`: one JSON object per line over a local socket.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from importlib import resources
from typing import Any

from blux_runtime.interfaces import ExecutionContext
from blux_runtime.registry import CapabilityRegistry
from blux_sdk.errors import BluxError, ErrorCode

PROTOCOL = "voidforge-integration/1"
DEFAULT_ENDPOINT = "@voidforge.integration.v1"
PROVIDER = {"repository": "Outer-Void/blux", "kind": "builtin"}
FORBIDDEN = frozenset({"grant", "authorization", "authorized", "approved", "approval", "confirmed", "confirmation",
                       "phrase", "typed_phrase", "force", "yolo", "write_barrier", "qualified", "dry-run", "nonce",
                       "token"})  # fmt: skip
# VoidForge operation class -> BLUX side-effect class. PRIVILEGED counts as destructive.
SIDE_EFFECTS = {None: "none", "READ_ONLY": "read", "SAFE_WRITE": "write", "PRIVILEGED": "destructive",
                "DESTRUCTIVE": "destructive"}  # fmt: skip


class Unavailable(Exception):
    pass


def catalog() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(
        resources.files("blux_runtime.devices").joinpath("voidforge_capabilities.json").read_text("utf-8")
    )
    if doc.get("protocol") != PROTOCOL:
        raise ValueError("unsupported VoidForge capability catalog")
    return doc


def exchange(request: dict[str, Any], endpoint: str | None = None, timeout: float = 330.0) -> dict[str, Any]:
    endpoint = endpoint or os.environ.get("VOIDFORGE_ENDPOINT") or DEFAULT_ENDPOINT
    if not hasattr(socket, "AF_UNIX"):
        raise Unavailable("local sockets are not supported on this platform")
    addr = "\0" + endpoint[1:] if endpoint.startswith("@") else endpoint
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(5)
        try:
            sock.connect(addr)
        except OSError as exc:
            raise Unavailable(f"VoidForge is not reachable at {endpoint} ({exc.strerror or exc})") from None
        sock.settimeout(timeout)
        sock.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                raise Unavailable("VoidForge closed the connection without a response")
            buf += chunk
            if len(buf) > 512 * 1024:
                raise Unavailable("VoidForge response too large")
        response: dict[str, Any] = json.loads(buf)
        return response
    except TimeoutError:
        raise Unavailable("VoidForge did not respond in time") from None
    finally:
        sock.close()


def _carries_authorization(value: Any) -> bool:
    if isinstance(value, dict):
        return any(str(k).lower() in FORBIDDEN or _carries_authorization(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_carries_authorization(v) for v in value)
    return False


def _input_schema(spec: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for name in spec["inputs"]:
        props[name] = (
            {"type": "string", "pattern": "^[0-9a-f]{64}$"}
            if name == "sha256"
            else {"type": "string", "minLength": 1, "maxLength": 4096}
        )
        if name == "target" and spec["targets"]:
            props[name] = {"type": "string", "enum": spec["targets"]}
    if spec["params"]:
        props["params"] = {
            "type": "object",
            "additionalProperties": False,
            "properties": {p: {"type": "string", "maxLength": 128} for p in spec["params"]},
        }
    required = [n for n, kind in spec["inputs"].items() if kind == "required"]
    return {"type": "object", "additionalProperties": False, "required": required, "properties": props}


OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["status", "code", "summary", "receipt"],
    "properties": {
        "status": {"type": "string"},
        "code": {"type": "string"},
        "summary": {"type": "string"},
        "details": {"type": "string"},
        "operation": {"type": ["object", "null"]},
        "receipt": {"type": "object"},
    },
}


def descriptor(spec: dict[str, Any]) -> dict[str, Any]:
    cid = spec["id"]
    side = SIDE_EFFECTS[spec["class"]]
    return {
        "contract": "blux.capability",
        "contract_version": "1.0",
        "capability_id": cid,
        "version": "1.0.0",
        "summary": f"VoidForge {cid}: {spec['outputs']} (authorization: {spec['authorization']})",
        "contract_versions": ["1.0"],
        "input_schema": _input_schema(spec),
        "output_schema": OUTPUT_SCHEMA,
        "side_effects": side,
        "required_permissions": [f"tool:{cid}"],
        "idempotent": side in ("none", "read"),
        "timeout_ms": 90_000 if side in ("none", "read") else 330_000,
        "provider": {**PROVIDER, "entrypoint": f"blux_runtime.devices.voidforge:{cid}"},
        "labels": {"kind": "tool", "device_operation": "true", "engine": "voidforge"},
    }


def _fail(message: str, **details: Any) -> BluxError:
    return BluxError(ErrorCode.EXECUTION_FAILED, message, details=details)


def register(registry: CapabilityRegistry, *, endpoint: str | None = None) -> list[str]:
    """Register every VoidForge capability as a BLUX tool. Returns their ids."""
    ids = []
    for spec in catalog()["capabilities"]:

        def handler(input: dict[str, Any], context: ExecutionContext, spec: dict[str, Any] = spec) -> dict[str, Any]:  # noqa: A002 - CapabilityHandler
            if _carries_authorization(input):
                raise _fail(
                    "Requests cannot carry authorization; confirm on the VoidForge device.", reason="elevation_refused"
                )
            request: dict[str, Any] = {
                "protocol": PROTOCOL,
                "kind": "call",
                "id": "blux-" + uuid.uuid4().hex[:16],
                "capability": spec["id"],
                **{k: input[k] for k in ("device", "target", "source", "sha256") if k in input},
                "params": dict(input.get("params", {})),
                "requester": {"kind": "blux", "session": context.session_id or context.operation_id},
            }
            try:
                response = exchange(request, endpoint)
            except Unavailable as exc:
                raise _fail(
                    f"{exc}. Open VoidForge and enable local integrations.", reason="voidforge_unavailable"
                ) from None
            status = response.get("status")
            if status == "completed":
                return {k: response.get(k) for k in ("status", "code", "summary", "details", "operation", "receipt")}
            raise _fail(
                f"VoidForge {status}: {response.get('summary')}",
                reason="voidforge_unavailable" if status == "unavailable" else f"voidforge_{status}",
                voidforge_code=response.get("code"),
                receipt=response.get("receipt"),
            )

        registry.register(descriptor(spec), handler)
        ids.append(spec["id"])
    return ids


def device_policy() -> dict[str, Any]:
    """BLUX policy for asking VoidForge: read for users, agents and services; writes for users and agents;
    privileged/destructive for users only. Permission to ask is not authorization: VoidForge still asks
    the person at the device."""
    by_side: dict[str, list[str]] = {}
    for spec in catalog()["capabilities"]:
        by_side.setdefault(SIDE_EFFECTS[spec["class"]], []).append(spec["id"])
    actors = {"none": ["user", "agent", "service"], "read": ["user", "agent", "service"], "write": ["user", "agent"],
              "destructive": ["user"]}  # fmt: skip
    rules = [
        {
            "rule_id": f"allow-voidforge-{side}",
            "effect": "allow",
            "capabilities": sorted(caps),
            "actor_types": actors[side],
            "max_side_effects": side,
            "grants": [f"tool:{c}" for c in sorted(caps)],
        }
        for side, caps in sorted(by_side.items())
    ]
    return {"contract": "blux.policy", "contract_version": "1.0", "policy_id": "blux-voidforge-devices",
            "default_effect": "deny", "rules": rules}  # fmt: skip
