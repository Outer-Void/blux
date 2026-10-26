"""File-write safety and workspace confinement (Stage 13, slice 4-5; parity matrix BLH-008).

The rule set must deny every write the BLHermes file_safety golden denies. BLUX is stricter in three
places:
- Legacy `auth.json` is denied.
- Shell start-up files and `~/.gitconfig` need approval instead of being silently writable.
- BLUX's and BluxGPT's own credential files are denied.

Paths are resolved (symlinks followed) before they are checked. Clean-room implementation; no upstream code.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

DENY_HOME = (
    ".ssh",
    ".aws/credentials",
    ".aws/config",
    ".netrc",
    ".gnupg",
    ".git-credentials",
    ".docker/config.json",
    ".kube/config",
    ".pgpass",
    ".config/gh/hosts.yml",
    ".config/bluxgpt/.env",
    ".hermes/.env",
    ".hermes/auth.json",
)
APPROVE_HOME = (
    ".bashrc",
    ".bash_profile",
    ".profile",
    ".zshrc",
    ".zprofile",
    ".zshenv",
    ".gitconfig",
    ".config/fish",
    ".termux/boot",
    ".config/autostart",
    ".local/bin",
)
DENY_SYSTEM = (
    "/etc",
    "/boot",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/sys",
    "/proc",
    "/dev",
    "/system",
    "/vendor",
)


class PathDenied(PermissionError):
    pass


def _home(environ: Mapping[str, str] | None) -> Path:
    env = os.environ if environ is None else environ
    return Path(env.get("HOME") or Path.home()).expanduser().resolve()


def _under(path: Path, base: Path) -> bool:
    return path == base or base in path.parents


def resolve(path: str | Path) -> Path:
    return Path(os.path.realpath(Path(path).expanduser()))


def write_denied(path: str | Path, environ: Mapping[str, str] | None = None) -> bool:
    p, home = resolve(path), _home(environ)
    env = os.environ if environ is None else environ
    extra = [Path(env[k]).expanduser().resolve() / ".env" for k in ("HERMES_HOME",) if env.get(k)]
    if any(_under(p, home / d) for d in DENY_HOME) or any(_under(p, e) for e in extra):
        return True
    return any(_under(p, Path(d)) for d in DENY_SYSTEM) and not _under(p, home)


def write_needs_approval(path: str | Path, environ: Mapping[str, str] | None = None) -> bool:
    p, home = resolve(path), _home(environ)
    return any(_under(p, home / d) for d in APPROVE_HOME)


def confine(path: str | Path, root: str | Path) -> Path:
    """Resolve `path` relative to `root`; refuse anything that escapes it (after resolving symlinks)."""
    base = resolve(root)
    p = resolve(base / path) if not Path(path).is_absolute() else resolve(path)
    if not _under(p, base):
        raise PathDenied(f"{path!s} is outside the workspace")
    return p
