"""`provider.kind: subprocess` capabilities (ADR-0009).

BLUX runs the declared entrypoint as an argv (never through a shell), writes one JSON request to stdin and
reads exactly one JSON object from stdout:

  exit 0  stdout is the capability output (then validated against output_schema by the executor)
  exit 1  stdout is {"error": {"code", "message", "details"}}: the provider failed (EXECUTION_FAILED)
  other   EXECUTION_FAILED; unparseable or oversized stdout is OUTPUT_VALIDATION_FAILED

stderr is drained and discarded: it is diagnostics, may contain anything, and never enters a result or
receipt. The child gets a minimal environment (PATH, HOME, locale, TMPDIR), not the caller's secrets.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Iterable, Mapping
from typing import Any

from blux_runtime.interfaces import ExecutionContext
from blux_sdk.capability import CapabilityDescriptor
from blux_sdk.errors import BluxError, ErrorCode
from blux_sdk.serialization import loads_json

MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_PROVIDER_MESSAGE = 512
ENV_ALLOW = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "PREFIX")


def minimal_env(source: Mapping[str, str] | None = None, allow: Iterable[str] = ENV_ALLOW) -> dict[str, str]:
    src = os.environ if source is None else source
    return {k: src[k] for k in allow if k in src}


class SubprocessProvider:
    def __init__(
        self,
        descriptor: CapabilityDescriptor,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if descriptor.provider.get("kind") != "subprocess":
            raise ValueError("descriptor is not a subprocess capability")
        entrypoint = descriptor.provider.get("entrypoint")
        if not entrypoint:
            raise ValueError(f"{descriptor.capability_id}: subprocess provider needs an entrypoint")
        self.argv = shlex.split(entrypoint)
        if not self.argv:
            raise ValueError(f"{descriptor.capability_id}: empty entrypoint")
        self.capability_id = descriptor.capability_id
        self.cwd = cwd
        self.env = dict(env) if env is not None else minimal_env()

    def _resolve(self) -> str:
        exe = shutil.which(self.argv[0], path=self.env.get("PATH"))
        if exe is None:
            raise BluxError(
                ErrorCode.EXECUTION_FAILED,
                "Provider executable not found.",
                details={"reason": "executable_not_found", "executable": self.argv[0]},
            )
        return exe

    def __call__(self, input: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:  # noqa: A002
        request = {
            "capability_id": context.capability_id,
            "input": input,
            "context": {
                "operation_id": context.operation_id,
                "parent_operation_id": context.parent_operation_id,
                "session_id": context.session_id,
                "project_id": context.project_id,
                "capability_id": context.capability_id,
                "actor": dict(context.actor),
                "timeout_ms": context.timeout_ms,
            },
        }
        try:
            proc = subprocess.run(  # noqa: S603 - argv from a validated descriptor, no shell
                [self._resolve(), *self.argv[1:]],
                input=json.dumps(request, sort_keys=True).encode("utf-8"),
                capture_output=True,
                cwd=self.cwd,
                env=self.env,
                timeout=context.timeout_ms / 1000,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise BluxError(
                ErrorCode.TIMEOUT,
                f"Provider exceeded {context.timeout_ms} ms.",
                details={"timeout_ms": context.timeout_ms},
            ) from None
        except OSError as exc:
            raise BluxError(
                ErrorCode.EXECUTION_FAILED,
                "Provider could not be started.",
                details={"reason": "spawn_failed", "errno": exc.errno},
            ) from None
        return self._interpret(proc.returncode, proc.stdout)

    def _interpret(self, code: int, stdout: bytes) -> dict[str, Any]:
        if len(stdout) > MAX_OUTPUT_BYTES:
            raise BluxError(
                ErrorCode.OUTPUT_VALIDATION_FAILED,
                "Provider output too large.",
                details={"reason": "output_too_large", "limit": MAX_OUTPUT_BYTES},
            )
        try:
            doc = loads_json(stdout, max_bytes=MAX_OUTPUT_BYTES)
        except BluxError as exc:
            raise BluxError(
                ErrorCode.OUTPUT_VALIDATION_FAILED if code == 0 else ErrorCode.EXECUTION_FAILED,
                "Provider did not print exactly one JSON object.",
                details={"reason": "protocol_violation", "exit_code": code, "parse": exc.details.get("reason")},
            ) from None
        if code == 0:
            return doc
        error = doc.get("error")
        provider_code = error.get("code") if isinstance(error, dict) else None
        if code == 1 and isinstance(error, dict) and isinstance(provider_code, str):
            raw_message = error.get("message")
            message = raw_message if isinstance(raw_message, str) else "Provider failed."
            details = error.get("details") if isinstance(error.get("details"), dict) else {}
            raise BluxError(
                ErrorCode.EXECUTION_FAILED,
                message[:MAX_PROVIDER_MESSAGE],
                details={"exit_code": code, "provider_code": provider_code[:128], "provider_details": details},
            )
        raise BluxError(
            ErrorCode.EXECUTION_FAILED,
            "Provider failed.",
            details={"reason": "nonzero_exit", "exit_code": code},
        )
