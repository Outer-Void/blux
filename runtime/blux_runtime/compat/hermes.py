"""Explicit, time-bounded import of legacy BLHermes/Hermes state (Stage 13, slices 2, 6 and 7).

This is the only BLUX code that reads HERMES_HOME or ~/.hermes, and it only runs when someone asks
(`blux migrate hermes`). It reads the legacy home and never writes or deletes there. Nothing here
imports BLHermes code: legacy state is read as files and SQLite rows.

  sessions     state.db sessions and messages (role, content, tool name, time). Hidden reasoning columns
               (`reasoning`, `reasoning_content`, `reasoning_details`, codex items) are never read.
  credentials  provider keys from <legacy home>/.env, written only on --apply to BluxGPT's config
               (0600, existing values kept). Values are never printed, logged or returned.

Expiry: the compatibility path is time-bounded (COMPAT_EXPIRES). After that date `plan()` refuses to
run, and the test suite fails as a reminder to remove this module.
"""

from __future__ import annotations

import datetime as _dt
import os
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from blux_runtime.sessions import SessionError, SessionStore

COMPAT_ID = "hermes-home-import"
COMPAT_EXPIRES = _dt.date(2027, 4, 30)
# Legacy provider variables that BluxGPT understands, by the name BluxGPT reads (identical names; values
# move only on --apply). Taken from the provider_registry golden.
PROVIDER_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY")
NEVER_READ_COLUMNS = (
    "reasoning",
    "reasoning_content",
    "reasoning_details",
    "codex_reasoning_items",
    "codex_message_items",
)


class CompatExpired(RuntimeError):
    pass


def check_not_expired(today: _dt.date | None = None) -> None:
    if (today or _dt.date.today()) > COMPAT_EXPIRES:
        raise CompatExpired(
            f"the {COMPAT_ID} compatibility path expired on {COMPAT_EXPIRES.isoformat()}; remove it (ADR-0013)"
        )


def legacy_home(environ: Mapping[str, str] | None = None) -> Path | None:
    env = os.environ if environ is None else environ
    candidates = [Path(env["HERMES_HOME"]).expanduser()] if env.get("HERMES_HOME") else []
    home = Path(env["HOME"]).expanduser() if env.get("HOME") else Path.home()
    candidates.append(home / ".hermes")
    return next((c for c in candidates if c.is_dir()), None)


@dataclass
class Plan:
    legacy_home: Path | None
    sessions: int = 0
    messages: int = 0
    already_imported: int = 0
    credential_names: list[str] = field(default_factory=list)
    skipped_credentials: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_document(self) -> dict[str, Any]:
        return {
            "contract": "blux.migration-plan",
            "compat_id": COMPAT_ID,
            "compat_expires": COMPAT_EXPIRES.isoformat(),
            "legacy_home": str(self.legacy_home) if self.legacy_home else None,
            "sessions": self.sessions,
            "messages": self.messages,
            "already_imported": self.already_imported,
            "credential_names": self.credential_names,
            "skipped_credentials": self.skipped_credentials,
            "notes": self.notes,
        }


def _readonly(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _env_names(path: Path) -> list[str]:
    names = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip().removeprefix("export ").strip()
        if line and not line.startswith("#") and "=" in line:
            names.append(line.split("=", 1)[0].strip())
    return names


def _env_values(path: Path) -> dict[str, str]:
    out = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip().removeprefix("export ").strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("'\"")
    return out


def plan(store: SessionStore, environ: Mapping[str, str] | None = None, *, today: _dt.date | None = None) -> Plan:
    check_not_expired(today)
    home = legacy_home(environ)
    p = Plan(home)
    if home is None:
        p.notes.append("no legacy home (HERMES_HOME or ~/.hermes) found")
        return p
    db = home / "state.db"
    if db.is_file():
        with _readonly(db) as conn:
            ids = [r["id"] for r in conn.execute("SELECT id FROM sessions")]
            p.messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        p.already_imported = sum(1 for i in ids if store.get_session(f"hermes-{i}") is not None)
        p.sessions = len(ids) - p.already_imported
    else:
        p.notes.append("no state.db")
    env_file = home / ".env"
    if env_file.is_file():
        names = _env_names(env_file)
        p.credential_names = [n for n in names if n in PROVIDER_KEYS]
        p.skipped_credentials = sorted(n for n in names if n not in PROVIDER_KEYS)
    return p


def import_sessions(store: SessionStore, home: Path) -> tuple[int, int]:
    """Copy legacy sessions and their explicit messages into the BLUX store. Idempotent."""
    db = home / "state.db"
    if not db.is_file():
        return 0, 0
    sessions = messages = 0
    with _readonly(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        title = "title" if "title" in cols else "NULL"
        for s in conn.execute(
            f"SELECT id, source, {title} AS title, model, started_at, ended_at, end_reason "  # noqa: S608 - fixed columns
            "FROM sessions ORDER BY started_at"
        ).fetchall():
            sid = f"hermes-{s['id']}"
            try:
                store.create_session(
                    sid,
                    s["source"] or "hermes",
                    title=s["title"],
                    model=s["model"],
                    imported_from="hermes",
                    started_at=s["started_at"],
                )
            except SessionError:
                continue  # already imported
            sessions += 1
            # Only explicit fields; hidden reasoning columns are never selected (NEVER_READ_COLUMNS).
            for m in conn.execute(
                "SELECT role, content, tool_name, timestamp FROM messages WHERE session_id = ? ORDER BY id", (s["id"],)
            ):
                if m["role"] in ("system", "user", "assistant", "tool") and m["content"]:
                    store.append_message(
                        sid, role=m["role"], content=m["content"], tool_name=m["tool_name"], created_at=m["timestamp"]
                    )
                    messages += 1
            if s["ended_at"]:
                store.end_session(sid, s["end_reason"] or "imported")
    return sessions, messages


def import_credentials(home: Path, bluxgpt_env: Path) -> list[str]:
    """Move supported provider keys into BluxGPT's .env (0600). Existing keys win. Returns names only."""
    src = home / ".env"
    if not src.is_file():
        return []
    values = {k: v for k, v in _env_values(src).items() if k in PROVIDER_KEYS and v}
    existing = _env_values(bluxgpt_env) if bluxgpt_env.is_file() else {}
    added = sorted(k for k in values if k not in existing)
    if not added:
        return []
    bluxgpt_env.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(bluxgpt_env, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write("\n# imported from the legacy Hermes home by `blux migrate hermes`\n")
        for k in added:
            fh.write(f"{k}={values[k]}\n")
    bluxgpt_env.chmod(0o600)
    return added


def apply(
    store: SessionStore,
    environ: Mapping[str, str] | None = None,
    *,
    bluxgpt_env: Path | None = None,
    credentials: bool = False,
    today: _dt.date | None = None,
) -> dict[str, Any]:
    check_not_expired(today)
    env = os.environ if environ is None else environ
    home = legacy_home(env)
    if home is None:
        return {"imported_sessions": 0, "imported_messages": 0, "imported_credentials": [], "legacy_home": None}
    s, m = import_sessions(store, home)
    creds: list[str] = []
    if credentials:
        target = bluxgpt_env or (
            Path(env["BLUXGPT_CONFIG"])
            if env.get("BLUXGPT_CONFIG")
            else Path(env.get("HOME", "~")).expanduser() / ".config" / "bluxgpt" / ".env"
        )
        creds = import_credentials(home, target)
    return {"imported_sessions": s, "imported_messages": m, "imported_credentials": creds, "legacy_home": str(home)}
