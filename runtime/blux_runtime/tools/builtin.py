"""Built-in tool capabilities: shell.exec, fs.read, fs.write (Stage 13, slice 4; parity matrix BLH-004/006).

Every tool is an ordinary BLUX capability (`labels.kind = tool`), so policy, the hook system (the tool.*
events) and receipts apply. On top of that:

- Workspace confinement. Paths resolve inside the workspace root (symlinks followed), or the call fails.
- Write safety. Credential and system paths are denied. Shell start-up files need an approval.
- Commands. Every command needs a one-time approval of that exact command, or YOLO switched on by the person
  for the session (OV-62, F95: this is not an OS sandbox). Device commands need a one-time approval every time;
  YOLO never covers them.
- Children. They get the allow-list environment (no credentials) and their own process group, and they are
  killed at the deadline. Output is capped and redacted.

Clean-room implementations of retained BLHermes behaviour; no upstream code.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import stat
import subprocess
import threading
import time
from importlib import resources
from pathlib import Path
from typing import IO, Any

from blux_runtime.interfaces import ExecutionContext
from blux_runtime.registry import CapabilityRegistry
from blux_runtime.security import (
    SessionApprovals,
    child_env,
    confine,
    detect,
    redact,
    workspace_write_needs_approval,
    write_denied,
    write_needs_approval,
)
from blux_runtime.security.commands import Detection
from blux_runtime.security.paths import PathDenied, open_confined
from blux_sdk.errors import BluxError, ErrorCode

MAX_OUTPUT = 64 * 1024
# Every shell command needs approval (F95): this stands in for commands the patterns call safe.
UNCONFINED = Detection(True, "shell.unconfined", "every shell command (shell.exec is not an OS sandbox)", False)
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


class _Capture:
    """Drain one pipe on a thread, keeping the first MAX_OUTPUT bytes and counting the rest (OV-50: a command's
    output no longer has to fit in memory before it is cut)."""

    def __init__(self, stream: IO[bytes] | None) -> None:
        self.data = bytearray()
        self.truncated = False
        self.stream = stream
        self.thread = threading.Thread(target=self._drain, daemon=True)
        self.thread.start()

    def _drain(self) -> None:
        stream = self.stream
        if stream is None:
            return
        with contextlib.suppress(OSError, ValueError):
            for chunk in iter(lambda: stream.read1(65536), b""):  # type: ignore[attr-defined]
                room = MAX_OUTPUT - len(self.data)
                if len(chunk) > room:
                    self.truncated = True
                self.data += chunk[: max(0, room)]

    def alive(self) -> bool:
        return self.thread.is_alive()

    def join(self) -> None:
        # A process that left the command's process group can hold the pipe open; never wait on it for long.
        self.thread.join(2)
        if self.stream is not None:
            with contextlib.suppress(OSError):
                self.stream.close()

    def text(self) -> str:
        return bytes(self.data).decode("utf-8", "replace")


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
        cwd = where(input.get("cwd", "."))  # an outside working directory is refused before any approval question
        detection = detect(command)
        # OV-62 (F95, owner decision 2026-11-09): shell.exec is not an OS sandbox, and pattern detection let
        # writes and deletions outside the workspace through. Every command now needs the person's approval of
        # that exact command (approve_once) or YOLO, which only the person can switch on for a session. A
        # session approval of a rule no longer covers shell commands.
        decision = gate.decide(_session(context), command, detection if detection.dangerous else UNCONFINED)
        if not decision.allowed or decision.basis not in ("one-time-approval", "yolo"):
            shown = detection if detection.dangerous else UNCONFINED
            raise _fail(
                f"Approval required: {shown.description}.",
                reason="approval_required",
                rule=shown.rule,
                device=detection.device,
            )
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
        out, err = _Capture(proc.stdout), _Capture(proc.stderr)
        deadline = time.monotonic() + max(0.1, context.timeout_ms / 1000 - 0.2)
        stopped = None
        try:
            # Like communicate(): finish when the shell has exited and both pipes are closed. Unlike it, keep
            # only MAX_OUTPUT bytes of each stream, and stop at the deadline or when the caller cancels.
            while proc.poll() is None or out.alive() or err.alive():
                if context.cancel.is_set():
                    stopped = "cancelled"
                    break
                if time.monotonic() >= deadline:
                    stopped = "timeout"
                    break
                time.sleep(0.02)
        finally:
            if stopped is not None or proc.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            out.join()
            err.join()
        if stopped == "cancelled":
            raise _fail("Command was cancelled and killed.", reason="cancelled")
        if stopped == "timeout":
            raise _fail("Command exceeded its timeout and was killed.", reason="timeout")
        return {
            "exit_code": proc.returncode,
            "stdout": redact(out.text()),
            "stderr": redact(err.text()),
            "truncated": out.truncated or err.truncated,
            "approval": decision.basis,
            "rule": detection.rule,
        }

    def fs_read(input: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:  # noqa: A002 - CapabilityHandler
        path = where(input["path"])
        if write_denied(path):
            raise _fail("Reading credential or system files is not allowed.", reason="denied_path")
        try:
            fd = open_confined(path, root, os.O_RDONLY)
        except PathDenied as exc:
            raise _fail(str(exc), reason="outside_workspace") from None
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            raise _fail("Not a file.", reason="not_found") from None
        with os.fdopen(fd, "rb") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                raise _fail("Not a file.", reason="not_found")
            data = fh.read()
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
        if write_needs_approval(path) or workspace_write_needs_approval(path, root):
            rule = f"fs.write:{path}"
            if not gate.is_approved(_session(context), rule):
                raise _fail(
                    "Approval required to change a start-up or configuration file.",
                    reason="approval_required",
                    rule=rule,
                )
            basis = "session-approval"
        data = input["content"].encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if input.get("append") else os.O_TRUNC)
        try:
            fd = open_confined(path, root, flags, make_parents=True)  # nothing swapped in is followed (OV-62)
        except PathDenied as exc:
            raise _fail(str(exc), reason="outside_workspace") from None
        with os.fdopen(fd, "ab" if input.get("append") else "wb") as fh:
            fh.write(data)
        return {"path": str(path.relative_to(root)), "bytes": len(data), "approval": basis}

    registry.register(
        _descriptor(
            "shell.exec",
            "Run a shell command in the workspace (every command needs approval).",
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
