"""BLUX session store: sessions, messages, full-text search (Stage 13, slice 7; parity matrix BLH-011).

SQLite at <profile home>/sessions.db (0600). Behaviour matches the BLHermes session_store golden: create a
session, append messages, search finds matching messages from either role, and ending records the reason
and time. BLUX keeps only what a conversation needs: no billing, compression bookkeeping or platform
routing columns, and never any hidden model reasoning (only the explicit message `content` is stored).

With a hook bus, the store emits session.created / session.loaded / session.closed.
Clean-room implementation; no upstream code.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from blux_runtime.hooks.bus import EventContext, HookBus, HookError
from blux_sdk.model_api import scrub

ROLES = ("system", "user", "assistant", "tool")
SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT,
    model TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT,
    imported_from TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_name TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id, id);
"""
FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, content='messages', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


class SessionError(ValueError):
    pass


class SessionStore:
    def __init__(self, path: Path, *, hooks: HookBus | None = None, redact: bool = True) -> None:
        self.path = Path(path)
        self.hooks = hooks
        self.redact = redact
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        new = not self.path.exists()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        if new:
            self.path.chmod(0o600)
        with self._db:
            self._db.executescript(SCHEMA)
            try:
                self._db.executescript(FTS)
                self.fts = True
            except sqlite3.OperationalError:
                self.fts = False  # SQLite without FTS5: search falls back to LIKE

    def close(self) -> None:
        self._db.close()

    # sessions -----------------------------------------------------------------------------------------

    def create_session(
        self,
        session_id: str,
        source: str,
        *,
        title: str | None = None,
        model: str | None = None,
        imported_from: str | None = None,
        started_at: float | None = None,
    ) -> str:
        with self._lock, self._db:
            if self._db.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone():
                raise SessionError(f"session {session_id!r} already exists")
            self._db.execute(
                "INSERT INTO sessions (id, source, title, model, started_at, imported_from) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, source, title, model, started_at or time.time(), imported_from),
            )
        self._emit("session.created", {"session_id": session_id, "client": source}, session_id)
        return session_id

    def load_session(self, session_id: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        if session is None:
            raise SessionError(f"no session {session_id!r}")
        self._emit("session.loaded", {"session_id": session_id, "client": session["source"]}, session_id)
        return session

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count "
            "FROM sessions s WHERE s.id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None

    def end_session(self, session_id: str, end_reason: str) -> None:
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE sessions SET ended_at = ?, end_reason = ? WHERE id = ? AND ended_at IS NULL",
                (time.time(), end_reason, session_id),
            )
        if cur.rowcount:
            self._emit("session.closed", {"session_id": session_id, "reason": end_reason}, session_id)

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # messages -----------------------------------------------------------------------------------------

    def append_message(
        self, session_id: str, *, role: str, content: str, tool_name: str | None = None, created_at: float | None = None
    ) -> int:
        if role not in ROLES:
            raise SessionError(f"unknown role {role!r}")
        text = scrub(content) if self.redact else content
        with self._lock, self._db:
            if not self._db.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone():
                raise SessionError(f"no session {session_id!r}")
            cur = self._db.execute(
                "INSERT INTO messages (session_id, role, content, tool_name, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, role, text, tool_name, created_at or time.time()),
            )
        return int(cur.lastrowid or 0)

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT id, role, content, tool_name, created_at FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_messages(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        if self.fts:
            terms = " ".join('"' + t.replace('"', '""') + '"' for t in query.split())
            sql = (
                "SELECT m.id, m.session_id, m.role, m.content FROM messages_fts f JOIN messages m ON m.id = f.rowid "
                "WHERE messages_fts MATCH ? ORDER BY m.id LIMIT ?"
            )
            rows = self._db.execute(sql, (terms, limit)).fetchall()
        else:
            like = "%" + query.replace("%", "\\%").replace("_", "\\_") + "%"
            rows = self._db.execute(
                "SELECT id, session_id, role, content FROM messages "
                "WHERE content LIKE ? ESCAPE '\\' ORDER BY id LIMIT ?",
                (like, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # hooks ---------------------------------------------------------------------------------------------

    def _emit(self, name: str, payload: dict[str, Any], session_id: str) -> None:
        if self.hooks is None:
            return
        with contextlib.suppress(HookError):  # notify events never break the store
            self.hooks.emit(name, payload, context=EventContext(session_id=session_id))
