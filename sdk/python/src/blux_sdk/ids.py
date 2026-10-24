"""Universal identifiers.

Generated IDs (operation, receipt, artifact, session) are a type prefix plus a ULID: 48-bit millisecond
timestamp and 80 random bits in Crockford base32, so they sort by creation time and need no coordination.
Declared IDs (project, capability, model) are chosen by humans and only validated here.
"""

from __future__ import annotations

import enum
import re
import secrets
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID = "[0-9A-HJKMNP-TV-Z]{26}"


class IdKind(str, enum.Enum):
    PROJECT = "project_id"
    CAPABILITY = "capability_id"
    OPERATION = "operation_id"
    PARENT_OPERATION = "parent_operation_id"
    RECEIPT = "receipt_id"
    ARTIFACT = "artifact_id"
    MODEL = "model_id"
    SESSION = "session_id"


PREFIXES: dict[IdKind, str] = {
    IdKind.OPERATION: "op",
    IdKind.PARENT_OPERATION: "op",
    IdKind.RECEIPT: "rcpt",
    IdKind.ARTIFACT: "art",
    IdKind.SESSION: "ses",
}

# Must match contracts/v1/common.schema.json; tests/contracts/test_schemas.py enforces this.
PATTERNS: dict[IdKind, re.Pattern[str]] = {
    IdKind.PROJECT: re.compile(r"^[a-z][a-z0-9-]{1,62}[a-z0-9]$"),
    IdKind.CAPABILITY: re.compile(r"^(?=.{1,128}$)[a-z][a-z0-9]*(\.[a-z][a-z0-9_-]*)+$"),
    IdKind.MODEL: re.compile(r"^(?=.{1,160}$)[a-z0-9][a-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9._:-]*$"),
    **{k: re.compile(rf"^{p}_{_ULID}$") for k, p in PREFIXES.items()},
}


def ulid(timestamp_ms: int | None = None) -> str:
    ms = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    if not 0 <= ms < 2**48:
        raise ValueError("ULID timestamp out of range")
    value = (ms << 80) | secrets.randbits(80)
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


def new_id(kind: IdKind) -> str:
    try:
        prefix = PREFIXES[kind]
    except KeyError:
        raise ValueError(f"{kind.value} is a declared identifier and cannot be generated") from None
    return f"{prefix}_{ulid()}"


def new_operation_id() -> str:
    return new_id(IdKind.OPERATION)


def new_receipt_id() -> str:
    return new_id(IdKind.RECEIPT)


def new_artifact_id() -> str:
    return new_id(IdKind.ARTIFACT)


def new_session_id() -> str:
    return new_id(IdKind.SESSION)


def is_valid(kind: IdKind, value: object) -> bool:
    return isinstance(value, str) and PATTERNS[kind].match(value) is not None


def timestamp_ms_of(value: str) -> int:
    """Recover the creation time (ms since epoch) embedded in a generated ID."""
    _, _, body = value.rpartition("_")
    if not re.fullmatch(_ULID, body):
        raise ValueError("not a generated BLUX identifier")
    n = 0
    for ch in body[:10]:
        n = n * 32 + _CROCKFORD.index(ch)
    return n
