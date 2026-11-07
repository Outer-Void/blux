"""Loader for a repository's .blux/ directory (docs/spec/dot-blux.md)."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from blux_sdk.capability import CapabilityDescriptor
from blux_sdk.errors import BluxError, ErrorCode
from blux_sdk.schemas import check_envelope
from blux_sdk.serialization import MAX_DOCUMENT_BYTES, loads_yaml

DOT_BLUX = ".blux"
MANIFEST = "manifest.yaml"


@dataclass(frozen=True)
class DotBlux:
    root: Path
    manifest: dict[str, Any]
    capability_set: dict[str, Any]
    policy: dict[str, Any]

    @property
    def project_id(self) -> str:
        return str(self.manifest["project_id"])

    def capabilities(self) -> list[CapabilityDescriptor]:
        return [CapabilityDescriptor.from_document(c) for c in self.capability_set["capabilities"]]


_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)


def _not_regular(path: Path) -> BluxError:
    return BluxError(
        ErrorCode.MALFORMED_INPUT,
        f"{path.name} is not a regular file (symbolic links, pipes and devices are refused).",
        details={"reason": "not_a_regular_file", "file": path.name},
    )


def read_bounded(path: Path, max_bytes: int = MAX_DOCUMENT_BYTES) -> bytes:
    """Read a regular file inside .blux/, at most max_bytes + 1 bytes (the parser reports too_large).

    Never follows a symbolic link (one could point outside the project) and never blocks on a pipe or reads
    a device without end, so one hostile project cannot hang or exhaust a scan of many projects.
    """
    if not hasattr(os, "O_NOFOLLOW") and path.is_symlink():  # pragma: no cover - Windows
        raise _not_regular(path)
    try:
        fd = os.open(path, _OPEN_FLAGS)
    except FileNotFoundError:
        raise BluxError(ErrorCode.INVALID_ENVELOPE, f"Missing {path.name}.", details={"file": path.name}) from None
    except OSError:
        if path.is_symlink():  # O_NOFOLLOW refused it (ELOOP, or EMLINK on some BSDs)
            raise _not_regular(path) from None
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise _not_regular(path)
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 1 << 16))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _read(path: Path, kind: str) -> dict[str, Any]:
    data = read_bounded(path)
    try:
        return check_envelope(loads_yaml(data), kind)
    except BluxError as exc:
        exc.details.setdefault("file", path.name)
        raise


def load(project_dir: str | Path) -> DotBlux:
    """Load and validate <project_dir>/.blux/{manifest,capabilities,policy}.yaml."""
    root = Path(project_dir) / DOT_BLUX
    if not root.is_dir():
        raise BluxError(ErrorCode.INVALID_ENVELOPE, "No .blux directory.", details={"file": DOT_BLUX})
    manifest = _read(root / MANIFEST, "project")
    # The schema restricts these to bare file names and read_bounded refuses links, so they cannot escape .blux/.
    capability_set = _read(root / manifest["capabilities"], "capability-set")
    policy = _read(root / manifest["policy"], "policy")
    return DotBlux(root=root, manifest=manifest, capability_set=capability_set, policy=policy)
