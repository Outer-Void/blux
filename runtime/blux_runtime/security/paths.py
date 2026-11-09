"""File-write safety and workspace confinement (Stage 13, slice 4-5; parity matrix BLH-008).

The rule set must deny every write the BLHermes file_safety golden denies. BLUX is stricter in three
places:
- Legacy `auth.json` is denied.
- Shell start-up files and `~/.gitconfig` need approval instead of being silently writable.
- BLUX's and BluxGPT's own credential files are denied.

Paths are resolved (symlinks followed) before they are checked. Clean-room implementation; no upstream code.
"""

from __future__ import annotations

import errno
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


# OV-50: inside a workspace, a repository's .git/ (hooks, config) and a .blux/ directory hold code or commands that
# run later, outside BLUX's approvals, so writing there needs an approval too.
APPROVE_WORKSPACE = (".git", ".blux")


def workspace_write_needs_approval(path: str | Path, root: str | Path) -> bool:
    """True when the resolved `path` lies in a .git/ or .blux/ directory anywhere below `root`."""
    p, base = resolve(path), resolve(root)
    if not _under(p, base) or p == base:
        return False
    return any(part in APPROVE_WORKSPACE for part in p.relative_to(base).parts)


def confine(path: str | Path, root: str | Path) -> Path:
    """Resolve `path` relative to `root`; refuse anything that escapes it (after resolving symlinks)."""
    base = resolve(root)
    p = resolve(base / path) if not Path(path).is_absolute() else resolve(path)
    if not _under(p, base):
        raise PathDenied(f"{path!s} is outside the workspace")
    return p


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_FD_WALK = _NOFOLLOW and _DIRECTORY and os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd


def open_confined(
    resolved: Path, root: str | Path, flags: int, *, mode: int = 0o644, make_parents: bool = False
) -> int:
    """Open a path `confine()` returned without following anything that changed since (OV-62).

    `confine()` resolves symlinks and checks the result, but a plain open() follows whatever is there when it runs:
    a directory swapped for a symlink in between redirected an fs.write outside the workspace (a drill wrote one
    file outside in 584 tries). Here every component of the resolved path is opened relative to its parent with
    O_NOFOLLOW, so a component that is now a symlink fails instead of being followed. Returns a file descriptor.
    """
    base = resolve(root)
    rel = resolved.relative_to(base)
    if not _FD_WALK:  # pragma: no cover - platforms without openat(); the resolved path is used as is
        if make_parents:
            resolved.parent.mkdir(parents=True, exist_ok=True)
        return os.open(resolved, flags, mode)
    fd = os.open(base, os.O_RDONLY | _DIRECTORY)
    try:
        for part in rel.parts[:-1]:
            try:
                nxt = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not make_parents:
                    raise
                os.mkdir(part, 0o755, dir_fd=fd)
                nxt = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return os.open(rel.parts[-1], flags | _NOFOLLOW, mode, dir_fd=fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise PathDenied(f"{rel} changed while it was being opened (a symlink appeared)") from None
        raise
    finally:
        os.close(fd)
