"""Contract version parsing and negotiation (ADR-0005).

A contract version is MAJOR.MINOR. A reader accepts exactly the versions in its supported set.
Major changes are breaking; a newer minor than the reader knows is rejected rather than guessed at,
because v1 schemas forbid unknown fields.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

CONTRACT_VERSION = "1.0"
SUPPORTED_CONTRACT_VERSIONS: tuple[str, ...] = ("1.0",)

_VERSION_RE = re.compile(r"^(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})$")


def parse_version(value: Any) -> tuple[int, int]:
    """Parse "MAJOR.MINOR". Raises BluxError(MALFORMED_CONTRACT_VERSION) for anything else."""
    from blux_sdk.errors import BluxError, ErrorCode

    if not isinstance(value, str) or not _VERSION_RE.match(value):
        raise BluxError(
            ErrorCode.MALFORMED_CONTRACT_VERSION,
            "contract_version must be a string of the form MAJOR.MINOR.",
            details={"received": _describe(value)},
        )
    major, minor = value.split(".")
    return int(major), int(minor)


def require_supported(value: Any, supported: Iterable[str] = SUPPORTED_CONTRACT_VERSIONS) -> str:
    """Return value if it is a supported contract version, else raise an explicit error."""
    from blux_sdk.errors import BluxError, ErrorCode

    parse_version(value)
    supported_list = sorted(set(supported), key=parse_version)
    if value not in supported_list:
        raise BluxError(
            ErrorCode.UNSUPPORTED_CONTRACT_VERSION,
            f"contract_version {value} is not supported.",
            details={"received": value, "supported": supported_list},
        )
    return str(value)


def negotiate(offered: Iterable[str], supported: Iterable[str] = SUPPORTED_CONTRACT_VERSIONS) -> str:
    """Pick the highest version present in both sets."""
    from blux_sdk.errors import BluxError, ErrorCode

    offered_list = list(offered)
    for v in offered_list:
        parse_version(v)
    common = set(offered_list) & set(supported)
    if not common:
        raise BluxError(
            ErrorCode.UNSUPPORTED_CONTRACT_VERSION,
            "No common contract version.",
            details={"offered": sorted(set(offered_list)), "supported": sorted(set(supported), key=parse_version)},
        )
    return max(common, key=parse_version)


def _describe(value: Any) -> Any:
    if isinstance(value, str):
        return value[:64]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"<{type(value).__name__}>"
