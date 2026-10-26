"""Built-in tool capabilities: shell.exec, fs.read, fs.write (Stage 13, slice 4; parity matrix BLH-004/006).

Every tool is an ordinary BLUX capability (`labels.kind = tool`), so policy, the hook system (the tool.*
events) and receipts apply. On top of that:

- Workspace confinement. Paths resolve inside the workspace root (symlinks followed), or the call fails.
- Write safety. Credential and system paths are denied. Shell start-up files need an approval.
- Commands. Dangerous commands need an approval in the calling session (SessionApprovals). Device commands
  need a one-time approval every time; YOLO never covers them.
- Children. They get the allow-list environment (no credentials) and their own process group, and they are
  killed at the deadline. Output is capped and redacted.

Clean-room implementations of retained BLHermes behaviour; no upstream code.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
from importlib import resources
from pathlib import Path
from typing import Any

from blux_runtime.interfaces import ExecutionContext
from blux_runtime.registry import CapabilityRegistry
from blux_runtime.security import (
    SessionApprovals,
    child_env,
    confine,
    detect,
    redact,
    write_denied,
    write_needs_approval,
)
from blux_runtime.security.paths import PathDenied
from blux_sdk.errors import BluxError, ErrorCode

MAX_OUTPUT = 64 * 1024
MAX_READ = 1024 * 1024
PROVIDER = {"repository": "Outer-Void/blux", "kind": "builtin"}


def _descriptor(
    cid: str, summary: str, side: str, inp: dict[str, Any], out: dict[str, Any], timeout: int
) -> dict[str, Any]:
    return {
        "contract": "blux.capability",
        "contract_version": "1.0",
        "capability_id": cid,
        "version": "1.0.0",
        "summary": summary,
        "contract_versions": ["1.0"],
        "input_schema": inp,
        "output_schema": out,
        "side_effects": side,
        "required_permissions": [f"tool:{cid}"],
        "idempotent": side == "read",
        "timeout_ms": timeout,
        "provider": {**PROVIDER, "entrypoint": f"blux_runtime.tools.builtin:{cid}"},
        "labels": {"kind": "tool"},
    }


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": required, "properties": props}


SHELL_IN = _obj(
    {"command": {"type": "string", "minLength": 1, "maxLength": 16384}, "cwd": {"type": "string"}}, ["command"]
)
SHELL_OUT = _obj(
    {
        "exit_code": {"type": "integer"},
        "stdout": {"type": "string"},
        "stderr": {"type": "string"},
        "truncated": {"type": "boolean"},
        "approval": {"type": "string"},
        "rule": {"type": ["string", "null"]},
    },
    ["exit_code", "stdout", "stderr", "truncated", "approval", "rule"],
)
READ_IN = _obj({"path": {"type": "string", "minLength": 1}}, ["path"])
READ_OUT = _obj(
    {
        "path": {"type": "string"},
        "content": {"type": "string"},
        "size": {"type": "integer"},
        "truncated": {"type": "boolean"},
    },
    ["path", "content", "size", "truncated"],
)
WRITE_IN = _obj(
    {"path": {"type": "string", "minLength": 1}, "content": {"type": "string"}, "append": {"type": "boolean"}},
    ["path", "content"],
)
WRITE_OUT = _obj(
    {"path": {"type": "string"}, "bytes": {"type": "integer"}, "approval": {"type": "string"}},
    ["path", "bytes", "approval"],
)


def tool_policy() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(
        resources.files("blux_runtime.tools").joinpath("tool_policy.json").read_text("utf-8")
    )
    return doc


def _fail(message: str, **details: Any) -> BluxError:
    return BluxError(ErrorCode.EXECUTION_FAILED, message, details=details)


def _session(ctx: ExecutionContext) -> str:
    return ctx.session_id or ctx.operation_id  # without a session, no approval can carry over


def register(
    registry: CapabilityRegistry, *, workspace: str | Path, approvals: SessionApprovals | None = None
) -> SessionApprovals:
    root = Path(workspace).expanduser().resolve()
    gate = approvals or SessionApprovals()

    def where(raw: str) -> Path:
        try:
            return confine(raw, root)
        except PathDenied as exc:
            raise _fail(str(exc), reason="outside_workspace") from None

    def shell_exec(input: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:  # noqa: A002 - CapabilityHandler
        command = input["command"]
        detection = detect(command)
        decision = gate.decide(_session(context), command, detection)
        if not decision.allowed:
            raise _fail(
                f"Approval required: {detection.description}.",
                reason="approval_required",
                rule=detection.rule,
                device=detection.device,
            )
        cwd = where(input.get("cwd", "."))
        sh = shutil.which("sh") or "/bin/sh"
        proc = subprocess.Popen(  # noqa: S603 - the command is the tool's input, gated above
            [sh, "-c", command],
            cwd=cwd,
            env=child_env(extra={"PWD": str(cwd)}),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            out, err = proc.communicate(timeout=max(0.1, context.timeout_ms / 1000 - 0.2))
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise _fail("Command exceeded its timeout and was killed.", reason="timeout") from None
        truncated = len(out) > MAX_OUTPUT or len(err) > MAX_OUTPUT
        return {
            "exit_code": proc.returncode,
            "stdout": redact(out[:MAX_OUTPUT].decode("utf-8", "replace")),
            "stderr": redact(err[:MAX_OUTPUT].decode("utf-8", "replace")),
            "truncated": truncated,
            "approval": decision.basis,
            "rule": detection.rule,
        }

    def fs_read(input: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:  # noqa: A002 - CapabilityHandler
        path = where(input["path"])
        if write_denied(path):
            raise _fail("Reading credential or system files is not allowed.", reason="denied_path")
        if not path.is_file():
            raise _fail("Not a file.", reason="not_found")
        data = path.read_bytes()
        return {
            "path": str(path.relative_to(root)),
            "content": redact(data[:MAX_READ].decode("utf-8", "replace")),
            "size": len(data),
            "truncated": len(data) > MAX_READ,
        }

    def fs_write(input: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:  # noqa: A002 - CapabilityHandler
        path = where(input["path"])
        if write_denied(path):
            raise _fail("Writing credential or system files is not allowed.", reason="denied_path")
        basis = "safe"
        if write_needs_approval(path):
            rule = f"fs.write:{path}"
            if not gate.is_approved(_session(context), rule):
                raise _fail(
                    "Approval required to change a start-up or configuration file.",
                    reason="approval_required",
                    rule=rule,
                )
            basis = "session-approval"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = input["content"].encode("utf-8")
        with path.open("ab" if input.get("append") else "wb") as fh:
            fh.write(data)
        return {"path": str(path.relative_to(root)), "bytes": len(data), "approval": basis}

    registry.register(
        _descriptor(
            "shell.exec",
            "Run a shell command in the workspace (approval for dangerous commands).",
            "destructive",
            SHELL_IN,
            SHELL_OUT,
            120000,
        ),
        shell_exec,
    )
    registry.register(
        _descriptor("fs.read", "Read a text file inside the workspace.", "read", READ_IN, READ_OUT, 10000), fs_read
    )
    registry.register(
        _descriptor("fs.write", "Write a text file inside the workspace.", "write", WRITE_IN, WRITE_OUT, 10000),
        fs_write,
    )
    return gate
