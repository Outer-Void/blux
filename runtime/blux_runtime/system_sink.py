"""Receipt sink that hands receipts to BLUX System (Outer-Void/blux-system).

BLUX emits receipts; BLUX System is the persistence authority (ADR-0002). The boundary is the `blux-system`
CLI and its one-JSON-document protocol (ADR-0008), so this repository does not import blux-system code.

Failure semantics (OV-52):
- the store is unavailable (missing executable, timeout, protocol failure, lock held, store missing): the receipt,
  or the artifact with its bytes, is appended to a local outbox (JSONL, mode 0600) and SystemSinkError is raised
  with `spooled: true`. `flush()` (`blux receipts flush`) sends the outbox later;
- the store refuses the document (invalid, conflict, corrupt store, or a refusal without a code): SystemSinkError
  is raised with `spooled: false`, and the document is set aside in `<outbox>.refused.jsonl`, never dropped.
  Retrying cannot fix a refusal; `flush(include_refused=True)` re-sends set-aside documents after a repair.

The outbox is shared by every BLUX process using the same store, so spooling and flushing are serialized with a
lock file; a flush first moves the outbox to `<outbox>.flushing`, so a crash during a flush loses nothing (the
store treats a re-sent identical document as a duplicate).
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from blux_sdk.serialization import canonical_json

try:  # POSIX advisory locking; elsewhere one BLUX process per outbox is assumed
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

# Codes for which a later retry can succeed: BLUX System was not reachable or was busy. Everything else
# (including SINK_REFUSED, a refusal without its own code) is a refusal.
# STORE_IO_ERROR (blux-system, OV-62): the disk was full or a size limit was hit; the store itself is consistent.
RETRYABLE_CODES = frozenset(
    {"STORE_LOCKED", "STORE_NOT_FOUND", "STORE_IO_ERROR", "SINK_UNAVAILABLE", "SINK_TIMEOUT", "SINK_PROTOCOL"}
)
ARTIFACT_ENTRY = "blux.outbox-artifact"


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

    def _send(self, receipt: dict[str, Any], extra: tuple[str, ...] = ()) -> dict[str, Any]:
        """Return the store's response document, or raise SystemSinkError(spooled=False)."""
        env = {k: v for k, v in os.environ.items() if k != "BLUX_SYSTEM_STORE"}
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [self.executable, "ingest", "--store", str(self.store), *extra, "-"],
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

    def preflight(self) -> None:
        """Check, before a high-risk capability acts, that the store would not refuse its receipt (OV-61, F114).

        Runs `blux-system verify` (read-only). A store that fails verification, or a refusal-class error, raises
        SystemSinkError(spooled=False) so the caller can stop before acting. An unavailable store (missing
        executable, timeout, protocol failure, lock held, store missing) passes: the receipt would be spooled and
        flushed later, exactly as for any other capability."""
        env = {k: v for k, v in os.environ.items() if k != "BLUX_SYSTEM_STORE"}
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [self.executable, "verify", "--store", str(self.store)],
                capture_output=True,
                timeout=self.timeout_s,
                env=env,
                check=False,
            )
            doc = json.loads(proc.stdout)
        except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return
        if not isinstance(doc, dict):
            return
        code = doc.get("code")
        if code is not None:
            if str(code) in RETRYABLE_CODES:
                return
            raise SystemSinkError(
                str(code), str(doc.get("message", "refused")), spooled=False, details={"store_error": doc}
            )
        problems = doc.get("problems") or []
        only_torn_tail = all(isinstance(p, dict) and p.get("reason") == "torn_tail" for p in problems)
        if doc.get("torn_tail") and problems and only_torn_tail:
            return  # an interrupted last write; the next ingest repairs it (OV-62)
        if proc.returncode != 0 or doc.get("ok") is not True:
            raise SystemSinkError(
                "STORE_VERIFY_FAILED",
                "blux-system verify reports a damaged store.",
                spooled=False,
                details={"store_error": doc},
            )

    def _retryable(self, exc: SystemSinkError) -> bool:
        return exc.code in RETRYABLE_CODES

    @property
    def refused_path(self) -> Path:
        return self.outbox.with_suffix(".refused.jsonl")

    @property
    def _flushing(self) -> Path:
        return self.outbox.with_suffix(".flushing.jsonl")

    @contextlib.contextmanager
    def _locked(self, name: str = ".lock") -> Iterator[bool]:
        """Hold `<outbox><name>` exclusively. Yields False instead of waiting when name is the flush lock and
        another flush holds it."""
        self.outbox.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.outbox.with_name(self.outbox.name + name), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if fcntl is None:
                yield True
                return
            if name == ".flush-lock":
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    yield False
                    return
            else:
                fcntl.flock(fd, fcntl.LOCK_EX)  # held only for short appends and renames
            try:
                yield True
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    @staticmethod
    def _append_lines(path: Path, entries: list[dict[str, Any]]) -> None:
        if not entries:
            return
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write("".join(canonical_json(e) + "\n" for e in entries))
            fh.flush()
            os.fsync(fh.fileno())

    def _spool(self, entry: dict[str, Any]) -> None:
        with self._locked():
            self._append_lines(self.outbox, [entry])

    def _set_aside(self, entry: dict[str, Any]) -> None:
        with self._locked():
            self._append_lines(self.refused_path, [entry])

    @staticmethod
    def _artifact_entry(descriptor: dict[str, Any], data: bytes) -> dict[str, Any]:
        return {"contract": ARTIFACT_ENTRY, "artifact": descriptor, "data_b64": base64.b64encode(data).decode("ascii")}

    def _send_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        if entry.get("contract") == ARTIFACT_ENTRY:
            return self._send_artifact(entry["artifact"], base64.b64decode(entry["data_b64"]))
        return self._send(entry)

    def _send_artifact(self, descriptor: dict[str, Any], data: bytes) -> dict[str, Any]:
        with tempfile.NamedTemporaryFile(prefix="blux-artifact-", delete=False) as fh:
            fh.write(data)
            path = fh.name
        try:
            return self._send(descriptor, ("--kind", "artifact", "--data", path))
        finally:
            os.unlink(path)

    def _deliver(self, entry: dict[str, Any]) -> None:
        """Send one entry now; on failure spool it (retryable) or set it aside (refused), then re-raise."""
        try:
            self._send_entry(entry)
        except SystemSinkError as exc:
            if self._retryable(exc):
                self._spool(entry)
                exc.spooled = True
                exc.details["outbox"] = str(self.outbox)
            else:
                self._set_aside(entry)
                exc.details["set_aside"] = str(self.refused_path)
            raise

    def emit_artifact(self, descriptor: dict[str, Any], data: bytes) -> None:
        """Store an artifact descriptor with its bytes (verified by blux-system against the descriptor's sha256).

        When the store is unavailable the artifact is spooled with its bytes, like a receipt (OV-52, F83)."""
        self._deliver(self._artifact_entry(descriptor, data))

    def emit(self, receipt: dict[str, Any]) -> None:
        self._deliver(receipt)

    def pending(self) -> dict[str, int]:
        """How many documents wait in the outbox (including an interrupted flush) and how many were set aside."""

        def count(path: Path) -> int:
            try:
                return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
            except FileNotFoundError:
                return 0

        return {"waiting": count(self.outbox) + count(self._flushing), "refused": count(self.refused_path)}

    def flush(self, *, include_refused: bool = False) -> dict[str, Any]:
        """Retry spooled documents. Accepted ones leave the outbox; refused ones move to <outbox>.refused.jsonl.

        Safe against concurrent spooling (F84) and against a crash mid-flush. A second flush running at the same
        time returns at once with `busy: true`."""
        with self._locked(".flush-lock") as mine:
            if not mine:
                return {"sent": 0, "remaining": 0, "refused": 0, "busy": True}
            with self._locked():
                moved = []
                for source in (self.outbox, *((self.refused_path,) if include_refused else ())):
                    if source.exists():
                        moved += self._read_entries(source)
                self._append_lines(self._flushing, moved)  # after a crash, .flushing already holds the rest
                for source in (self.outbox, *((self.refused_path,) if include_refused else ())):
                    if source.exists():
                        source.unlink()
            pending = self._read_entries(self._flushing) if self._flushing.exists() else []
            remaining, refused_docs, sent = [], [], 0
            for entry in pending:
                try:
                    self._send_entry(entry)
                    sent += 1
                except SystemSinkError as exc:
                    if self._retryable(exc):
                        remaining.append(entry)
                    else:
                        refused_docs.append(entry)  # cannot succeed on retry; kept aside, never dropped
            with self._locked():
                self._append_lines(self.outbox, remaining)
                self._append_lines(self.refused_path, refused_docs)
                if self._flushing.exists():
                    self._flushing.unlink()
            return {"sent": sent, "remaining": len(remaining), "refused": len(refused_docs)}

    @staticmethod
    def _read_entries(path: Path) -> list[dict[str, Any]]:
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn final line from a crash mid-append; the document was reported as not recorded
        return out
