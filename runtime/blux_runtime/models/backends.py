"""Model backends: where the router sends a request for one route (ADR-0010).

A backend is a separate program (blux-gpt is the reference one) speaking the model-backend protocol:

  stdin   one JSON object {"model_api_version", "request", "route", "context"}
  stdout  blux.model-stream-event objects, one per line; the last is "end" (with the response) or "error"
  exit    0 after an "end" or "error" event; anything else is BACKEND_PROTOCOL_ERROR

The backend owns provider transport, retries, streaming, timeouts and normalization. BLUX owns routing,
fallback, validation, policy and receipts. The child runs with a minimal environment plus only the
environment variables the route names in `credential_env`; their values never enter a request, event,
response, error or receipt. On cancellation or deadline the child is terminated, then killed.
"""

from __future__ import annotations

import json
import os
import queue
import shlex
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from blux_runtime.subprocess_provider import minimal_env
from blux_sdk import model_api
from blux_sdk.model_api import ModelApiError

MAX_LINE_BYTES = 8 * 1024 * 1024
EventSink = Callable[[dict[str, Any]], None]


class ModelBackend(Protocol):
    def generate(
        self,
        request: dict[str, Any],
        route: dict[str, Any],
        *,
        deadline: float,
        cancel: threading.Event,
        on_event: EventSink,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a blux.model-response or raise ModelApiError. Forward delta/tool_call/usage events."""
        ...


class SubprocessModelBackend:
    def __init__(self, name: str, entrypoint: str, *, env: Mapping[str, str] | None = None) -> None:
        self.name = name
        self.argv = shlex.split(entrypoint)
        if not self.argv:
            raise ValueError(f"backend {name}: empty entrypoint")
        self.source_env = dict(os.environ if env is None else env)

    def _env(self, route: Mapping[str, Any]) -> dict[str, str]:
        env = minimal_env(self.source_env)
        for name in route.get("credential_env") or []:
            if name in self.source_env:
                env[name] = self.source_env[name]
        return env

    def generate(
        self,
        request: dict[str, Any],
        route: dict[str, Any],
        *,
        deadline: float,
        cancel: threading.Event,
        on_event: EventSink,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        env = self._env(route)
        exe = shutil.which(self.argv[0], path=env.get("PATH"))
        if exe is None:
            raise ModelApiError(
                "PROVIDER_NOT_CONFIGURED",
                f"Model backend {self.name!r} is not installed.",
                provider=route["provider"],
                route_id=route["route_id"],
                details={"backend": self.name, "reason": "executable_not_found"},
            )
        payload = {
            "model_api_version": model_api.MODEL_API_VERSION,
            "request": request,
            "route": {k: v for k, v in route.items() if k != "credential_env"},
            "context": {**context, "deadline_ms": max(1, int((deadline - time.monotonic()) * 1000))},
        }
        try:
            proc = subprocess.Popen(  # noqa: S603 - argv from the routing table, no shell
                [exe, *self.argv[1:]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env
            )
        except OSError as exc:
            raise ModelApiError(
                "PROVIDER_NOT_CONFIGURED",
                f"Model backend {self.name!r} could not start.",
                provider=route["provider"],
                route_id=route["route_id"],
                details={"backend": self.name, "errno": exc.errno},
            ) from None
        assert proc.stdin is not None and proc.stdout is not None
        lines: queue.Queue[bytes | None] = queue.Queue()

        def reader() -> None:
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, b""):
                lines.put(line)
            lines.put(None)

        threading.Thread(target=reader, daemon=True).start()
        try:
            proc.stdin.write(json.dumps(payload).encode("utf-8"))
            proc.stdin.close()
        except BrokenPipeError:
            pass
        try:
            return self._read(proc, lines, route, deadline=deadline, cancel=cancel, on_event=on_event)
        finally:
            _stop(proc)

    def _read(
        self,
        proc: subprocess.Popen[bytes],
        lines: queue.Queue[bytes | None],
        route: dict[str, Any],
        *,
        deadline: float,
        cancel: threading.Event,
        on_event: EventSink,
    ) -> dict[str, Any]:
        def fail(code: str, message: str, **details: Any) -> ModelApiError:
            return ModelApiError(
                code,
                message,
                provider=route["provider"],
                route_id=route["route_id"],
                details={"backend": self.name, **details},
            )

        while True:
            if cancel.is_set():
                raise fail("CANCELLED", "Cancelled by the caller.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise fail("PROVIDER_TIMEOUT", "The backend did not finish before the deadline.")
            try:
                line = lines.get(timeout=min(remaining, 0.05))
            except queue.Empty:
                continue
            if line is None:
                raise fail(
                    "BACKEND_PROTOCOL_ERROR",
                    "The backend exited without an end or error event.",
                    exit_code=proc.wait(timeout=5),
                )
            if len(line) > MAX_LINE_BYTES:
                raise fail("BACKEND_PROTOCOL_ERROR", "A backend event exceeded the size limit.")
            try:
                event = json.loads(line)
            except ValueError:
                raise fail(
                    "BACKEND_PROTOCOL_ERROR", "The backend printed something that is not a JSON event."
                ) from None
            if model_api.schema_errors("stream-event", event):
                raise fail(
                    "BACKEND_PROTOCOL_ERROR",
                    "The backend printed an invalid stream event.",
                    errors=model_api.schema_errors("stream-event", event)[:5],
                )
            kind = event["type"]
            if kind == "end":
                return dict(event["data"]["response"])
            if kind == "error":
                err = ModelApiError.from_document(event["data"]["error"])
                err.provider = err.provider or route["provider"]
                err.route_id = err.route_id or route["route_id"]
                raise err
            if kind in ("delta", "tool_call", "usage"):
                on_event(event)


def _stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


class CallableBackend:
    """An in-process backend for tests and embedding: fn(request, route, emit) -> response document."""

    def __init__(self, fn: Callable[[dict[str, Any], dict[str, Any], EventSink], dict[str, Any]]) -> None:
        self.fn = fn

    def generate(
        self,
        request: dict[str, Any],
        route: dict[str, Any],
        *,
        deadline: float,
        cancel: threading.Event,
        on_event: EventSink,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if cancel.is_set():
            raise ModelApiError(
                "CANCELLED", "Cancelled by the caller.", provider=route["provider"], route_id=route["route_id"]
            )
        return self.fn(request, route, on_event)
