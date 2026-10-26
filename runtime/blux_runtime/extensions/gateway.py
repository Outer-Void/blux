"""Local execution gateway: an authenticated HTTP surface over the runtime (Stage 14; parity matrix BLH-021).

    POST /v1/execute        body: blux.execution, header: Authorization: Bearer <token>
    GET  /v1/capabilities   header: Authorization: Bearer <token>
    GET  /v1/health         no authentication; reveals only {"status": "ok"}

Rules:
- **Authorization first.** It is checked before the body is read, by a constant-time comparison against
  a token of at least 32 characters. The token comes from the environment
  (`BLUX_GATEWAY_TOKEN`), never from a file BLUX writes.
- **Callers cannot pick an identity.** The actor is forced to `{"type": "service", "id": "gateway"}`;
  policy decides what the gateway may run.
- **Bind address.** Loopback only, unless `allow_remote=True` is passed explicitly.
- **Body limits.** Bodies are capped at 1 MiB.

Messaging-platform adapters (Telegram, Discord, ...) are not part of this stage. They will run behind this
authorization path. Clean-room implementation; no upstream code.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from blux_runtime.executor import Runtime
from blux_sdk.errors import BluxError, ErrorCode
from blux_sdk.serialization import dumps_json

MAX_BODY = 1024 * 1024
GATEWAY_ACTOR = {"type": "service", "id": "gateway"}


class GatewayError(ValueError):
    pass


class Gateway:
    def __init__(
        self, runtime: Runtime, token: str, *, host: str = "127.0.0.1", port: int = 0, allow_remote: bool = False
    ) -> None:
        if not token or len(token) < 32:
            raise GatewayError("gateway token must be at least 32 characters (set BLUX_GATEWAY_TOKEN)")
        if not allow_remote and not ipaddress.ip_address(host).is_loopback:
            raise GatewayError(f"refusing to bind {host}: loopback only unless allow_remote=True")
        self.runtime, self._token = runtime, token.encode("utf-8")
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "blux-gateway"
            sys_version = ""

            def log_message(self, *args: Any) -> None:  # no request logging (bodies and tokens stay private)
                return

            def _reply(self, status: int, doc: dict[str, Any]) -> None:
                body = (dumps_json(doc) + "\n").encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self) -> bool:
                header = self.headers.get("Authorization", "")
                ok = header.startswith("Bearer ") and hmac.compare_digest(header[7:].encode("utf-8"), gateway._token)
                if not ok:
                    self._reply(401, {"error": "unauthorized"})
                return ok

            def do_GET(self) -> None:
                if self.path == "/v1/health":
                    return self._reply(200, {"status": "ok"})
                if self.path == "/v1/capabilities":
                    if self._authorized():
                        self._reply(200, gateway.runtime.registry.to_capability_set())
                    return None
                return self._reply(404, {"error": "not_found"})

            def do_POST(self) -> None:
                if self.path != "/v1/execute":
                    return self._reply(404, {"error": "not_found"})
                if not self._authorized():
                    return None
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    return self._reply(400, {"error": "bad_length"})
                if length <= 0 or length > MAX_BODY:
                    return self._reply(413 if length > MAX_BODY else 400, {"error": "body_size"})
                try:
                    doc = json.loads(self.rfile.read(length))
                except ValueError:
                    return self._reply(400, BluxError(ErrorCode.MALFORMED_INPUT, "Body is not JSON.").to_document())
                if isinstance(doc, dict):
                    doc["actor"] = dict(GATEWAY_ACTOR)  # callers never choose their identity
                outcome = gateway.runtime.execute(doc)
                return self._reply(200, outcome.result)

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        host, port = self.server.server_address[:2]
        return str(host), int(port)

    def start(self) -> Gateway:
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="blux-gateway")
        self._thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
