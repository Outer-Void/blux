"""Receipt sink that hands receipts to BLUX System (Outer-Void/blux-system).

BLUX emits receipts; BLUX System is the persistence authority (ADR-0002). The boundary is the `blux-system`
CLI and its one-JSON-document protocol (ADR-0008), so this repository does not import blux-system code.

Failure semantics:
- the store is unavailable (missing executable, timeout, lock held, store missing): the receipt is appended to
  a local outbox (JSONL) and SystemSinkError is raised. `flush()` retries the outbox later;
- the store refuses the receipt (invalid, conflict): SystemSinkError is raised and nothing is spooled,
  because retrying cannot fix it.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from blux_sdk.serialization import canonical_json

# Store error codes for which a later retry can succeed.
RETRYABLE_CODES = frozenset({"STORE_LOCKED", "STORE_NOT_FOUND"})


class SystemSinkError(Exception):
    def __init__(self, code: str, message: str, *, spooled: bool, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.spooled = spooled
        self.details = dict(details or {})

    def to_document(self) -> dict[str, Any]:
        return {
            "sink": "blux-system",
            "code": self.code,
            "message": self.message,
            "spooled": self.spooled,
            "details": self.details,
        }


class BluxSystemSink:
    def __init__(
        self,
        store: str | Path,
        *,
        executable: str = "blux-system",
        outbox: str | Path | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.store = Path(store)
        self.executable = executable
        self.outbox = Path(outbox) if outbox else self.store.with_name(self.store.name + ".outbox.jsonl")
        self.timeout_s = timeout_s

    def _send(self, receipt: dict[str, Any]) -> dict[str, Any]:
        """Return the store's response document, or raise SystemSinkError(spooled=False)."""
        env = {k: v for k, v in os.environ.items() if k != "BLUX_SYSTEM_STORE"}
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [self.executable, "ingest", "--store", str(self.store), "-"],
                input=canonical_json(receipt).encode("utf-8"),
                capture_output=True,
                timeout=self.timeout_s,
                env=env,
                check=False,
            )
        except FileNotFoundError:
            raise SystemSinkError("SINK_UNAVAILABLE", f"{self.executable} not found.", spooled=False) from None
        except subprocess.TimeoutExpired:
            raise SystemSinkError("SINK_TIMEOUT", "blux-system did not answer in time.", spooled=False) from None
        try:
            doc = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise SystemSinkError(
                "SINK_PROTOCOL",
                "blux-system did not return a JSON document.",
                spooled=False,
                details={"exit": proc.returncode},
            ) from None
        if proc.returncode == 0 and doc.get("status") in ("stored", "duplicate"):
            return dict(doc)
        raise SystemSinkError(
            str(doc.get("code", "SINK_REFUSED")),
            str(doc.get("message", "refused")),
            spooled=False,
            details={"store_error": doc},
        )

    def _retryable(self, exc: SystemSinkError) -> bool:
        return exc.code in RETRYABLE_CODES or exc.code.startswith("SINK_")

    def _spool(self, receipt: dict[str, Any]) -> None:
        self.outbox.parent.mkdir(parents=True, exist_ok=True)
        with self.outbox.open("a", encoding="utf-8") as fh:
            fh.write(canonical_json(receipt) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def emit(self, receipt: dict[str, Any]) -> None:
        try:
            self._send(receipt)
        except SystemSinkError as exc:
            if self._retryable(exc):
                self._spool(receipt)
                exc.spooled = True
                exc.details["outbox"] = str(self.outbox)
            raise

    def flush(self) -> dict[str, int]:
        """Retry spooled receipts. Accepted ones leave the outbox; refused ones move to <outbox>.refused.jsonl."""
        if not self.outbox.exists():
            return {"sent": 0, "remaining": 0, "refused": 0}
        pending = [json.loads(line) for line in self.outbox.read_text(encoding="utf-8").splitlines() if line.strip()]
        remaining, refused_docs, sent = [], [], 0
        for receipt in pending:
            try:
                self._send(receipt)
                sent += 1
            except SystemSinkError as exc:
                if self._retryable(exc):
                    remaining.append(receipt)
                else:
                    refused_docs.append(receipt)  # cannot succeed on retry; kept aside, never dropped
        if refused_docs:
            with self.outbox.with_suffix(".refused.jsonl").open("a", encoding="utf-8") as fh:
                fh.write("".join(canonical_json(r) + "\n" for r in refused_docs))
        tmp = self.outbox.with_suffix(".tmp")
        tmp.write_text("".join(canonical_json(r) + "\n" for r in remaining), encoding="utf-8")
        os.replace(tmp, self.outbox)
        if not remaining:
            self.outbox.unlink()
        return {"sent": sent, "remaining": len(remaining), "refused": len(refused_docs)}
