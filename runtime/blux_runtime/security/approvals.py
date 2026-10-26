"""Session-scoped approvals and YOLO (Stage 13, slice 5; parity matrix BLH-007).

Semantics follow the BLHermes approval_session_state golden. An approval holds only for the session that
granted it. YOLO is per session. Clearing a session removes both.

BLUX adds two rules:
- Device rules (`Detection.device`) are never covered by a session approval or by YOLO. Each such
  command needs its own one-time approval (`approve_once`), consumed when used.
- Every decision is returned as data, so the caller records it in receipts.

Clean-room implementation; no upstream code.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from blux_runtime.security.commands import Detection


@dataclass(frozen=True)
class Decision:
    allowed: bool
    basis: str  # "safe", "session-approval", "one-time-approval", "yolo", "needs-approval"
    rule: str | None


class SessionApprovals:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._approved: dict[str, set[str]] = {}
        self._yolo: set[str] = set()
        self._once: dict[str, set[str]] = {}

    def approve_session(self, session: str, rule: str) -> None:
        with self._lock:
            self._approved.setdefault(session, set()).add(rule)

    def approve_once(self, session: str, command: str) -> None:
        with self._lock:
            self._once.setdefault(session, set()).add(command)

    def is_approved(self, session: str, rule: str) -> bool:
        with self._lock:
            return rule in self._approved.get(session, set())

    def enable_yolo(self, session: str) -> None:
        with self._lock:
            self._yolo.add(session)

    def disable_yolo(self, session: str) -> None:
        with self._lock:
            self._yolo.discard(session)

    def yolo(self, session: str) -> bool:
        with self._lock:
            return session in self._yolo

    def clear(self, session: str) -> None:
        with self._lock:
            self._approved.pop(session, None)
            self._once.pop(session, None)
            self._yolo.discard(session)

    def decide(self, session: str, command: str, detection: Detection) -> Decision:
        if not detection.dangerous:
            return Decision(True, "safe", None)
        with self._lock:
            once = self._once.get(session, set())
            if command in once:
                once.discard(command)  # consumed
                return Decision(True, "one-time-approval", detection.rule)
            if detection.device:
                return Decision(False, "needs-approval", detection.rule)  # never by session approval or YOLO
            if detection.rule in self._approved.get(session, set()):
                return Decision(True, "session-approval", detection.rule)
            if session in self._yolo:
                return Decision(True, "yolo", detection.rule)
        return Decision(False, "needs-approval", detection.rule)
