"""Loader for a repository's .blux/ directory (docs/spec/dot-blux.md)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from blux_sdk.capability import CapabilityDescriptor
from blux_sdk.errors import BluxError, ErrorCode
from blux_sdk.schemas import check_envelope
from blux_sdk.serialization import loads_yaml

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


def _read(path: Path, kind: str) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise BluxError(ErrorCode.INVALID_ENVELOPE, f"Missing {path.name}.", details={"file": path.name}) from None
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
    # The schema restricts these to bare file names, so they cannot escape .blux/.
    capability_set = _read(root / manifest["capabilities"], "capability-set")
    policy = _read(root / manifest["policy"], "policy")
    return DotBlux(root=root, manifest=manifest, capability_set=capability_set, policy=policy)
