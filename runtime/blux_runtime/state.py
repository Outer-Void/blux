"""BLUX state root and profiles (Stage 13, slice 1-2; parity matrix BLH-012).

    BLUX_HOME            $BLUX_HOME, else $XDG_STATE_HOME/blux, else ~/.local/state/blux
    profile home         BLUX_HOME itself for the "default" profile; BLUX_HOME/profiles/<name> otherwise
    active profile       $BLUX_PROFILE (a name), else "default"

The layout mirrors the profile semantics characterized from BLHermes (paths golden): a named profile is a
directory under `profiles/`, and the root is the "default" profile. The names and environment variables
are BLUX's own. The legacy HERMES_HOME is never used as BLUX state. It is only read by the explicit,
time-bounded importer in `blux_runtime.compat.hermes`.

Clean-room implementation against migration/blhermes/characterization/golden/paths.json. No upstream code.
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

PROFILE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
DEFAULT_PROFILE = "default"


class StateError(ValueError):
    pass


def blux_home(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    if env.get("BLUX_HOME"):
        return Path(env["BLUX_HOME"]).expanduser()
    if env.get("XDG_STATE_HOME"):
        return Path(env["XDG_STATE_HOME"]).expanduser() / "blux"
    home = Path(env["HOME"]).expanduser() if env.get("HOME") else Path.home()
    return home / ".local" / "state" / "blux"


def profile_home(name: str = DEFAULT_PROFILE, environ: Mapping[str, str] | None = None) -> Path:
    if not PROFILE_NAME.match(name):
        raise StateError(f"invalid profile name {name!r} (lowercase letters, digits, '-', '_')")
    root = blux_home(environ)
    return root if name == DEFAULT_PROFILE else root / "profiles" / name


def profile_name_for_home(path: str | Path, environ: Mapping[str, str] | None = None) -> str | None:
    """The profile a directory belongs to, or None when it is not a BLUX profile home."""
    root = blux_home(environ).resolve()
    p = Path(path).expanduser().resolve()
    if p == root:
        return DEFAULT_PROFILE
    if p.parent == root / "profiles" and PROFILE_NAME.match(p.name):
        return p.name
    return None


@dataclass(frozen=True)
class StateLayout:
    """Where one profile keeps its state. Directories are created only by `ensure()`."""

    home: Path

    @property
    def sessions_db(self) -> Path:
        return self.home / "sessions.db"

    @property
    def approvals(self) -> Path:
        return self.home / "approvals.json"

    @property
    def migrations(self) -> Path:
        return self.home / "migrations"

    def ensure(self) -> StateLayout:
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        with contextlib.suppress(OSError):
            self.home.chmod(0o700)
        return self


def active_layout(environ: Mapping[str, str] | None = None) -> StateLayout:
    env = os.environ if environ is None else environ
    return StateLayout(profile_home(env.get("BLUX_PROFILE") or DEFAULT_PROFILE, env))
