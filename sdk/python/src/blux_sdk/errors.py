"""Error model (ADR-0006): one exception type, one wire shape, a closed set of codes."""

from __future__ import annotations

import enum
from typing import Any

from blux_sdk.versions import CONTRACT_VERSION


class ErrorCode(str, enum.Enum):
    MALFORMED_INPUT = "MALFORMED_INPUT"
    INVALID_ENVELOPE = "INVALID_ENVELOPE"
    MALFORMED_CONTRACT_VERSION = "MALFORMED_CONTRACT_VERSION"
    UNSUPPORTED_CONTRACT_VERSION = "UNSUPPORTED_CONTRACT_VERSION"
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    UNKNOWN_CAPABILITY = "UNKNOWN_CAPABILITY"
    CAPABILITY_VERSION_MISMATCH = "CAPABILITY_VERSION_MISMATCH"
    POLICY_DENIED = "POLICY_DENIED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    TIMEOUT = "TIMEOUT"
    OUTPUT_VALIDATION_FAILED = "OUTPUT_VALIDATION_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# Codes for which the identical request may succeed later without modification.
RETRYABLE_CODES = frozenset({ErrorCode.TIMEOUT})

# Codes raised before a capability handler runs. A result carrying one has status "rejected".
REJECTION_CODES = frozenset(
    {
        ErrorCode.MALFORMED_INPUT,
        ErrorCode.INVALID_ENVELOPE,
        ErrorCode.MALFORMED_CONTRACT_VERSION,
        ErrorCode.UNSUPPORTED_CONTRACT_VERSION,
        ErrorCode.SCHEMA_VALIDATION_FAILED,
        ErrorCode.UNKNOWN_CAPABILITY,
        ErrorCode.CAPABILITY_VERSION_MISMATCH,
        ErrorCode.POLICY_DENIED,
    }
)

_MAX_MESSAGE = 2048


class BluxError(Exception):
    """A contract-level failure with a stable machine-readable code."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = dict(details or {})
        self.retryable = code in RETRYABLE_CODES if retryable is None else retryable

    def to_document(self, *, operation_id: str | None = None) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "contract": "blux.error",
            "contract_version": CONTRACT_VERSION,
            "code": self.code.value,
            "message": (self.message or self.code.value)[:_MAX_MESSAGE],
            "retryable": self.retryable,
            "details": self.details,
        }
        if operation_id is not None:
            doc["operation_id"] = operation_id
        return doc

    def __repr__(self) -> str:
        return f"BluxError({self.code.value}, {self.message!r})"


def normalize_error(exc: BaseException) -> BluxError:
    """Map any exception to a BluxError without leaking tracebacks or exception text into details."""
    if isinstance(exc, BluxError):
        return exc
    if isinstance(exc, TimeoutError):
        return BluxError(ErrorCode.TIMEOUT, "Operation timed out.", details={"exception_type": "TimeoutError"})
    return BluxError(
        ErrorCode.EXECUTION_FAILED,
        "Capability execution raised an exception.",
        details={"exception_type": type(exc).__name__},
    )
