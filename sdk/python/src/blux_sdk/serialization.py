"""Strict parsing and canonical serialization.

Inputs are parsed defensively: duplicate keys, NaN/Infinity, non-UTF-8 bytes, oversized payloads and
non-object documents are MALFORMED_INPUT. Output is canonical JSON (sorted keys, no insignificant
whitespace, UTF-8) so hashes in receipts are reproducible.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import yaml

from blux_sdk.errors import BluxError, ErrorCode

MAX_DOCUMENT_BYTES = 1024 * 1024


def _malformed(reason: str, **details: Any) -> BluxError:
    return BluxError(ErrorCode.MALFORMED_INPUT, f"Malformed input: {reason}.", details={"reason": reason, **details})


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise _malformed("duplicate_key", key=key[:128])
        out[key] = value
    return out


def _reject_constant(name: str) -> Any:
    raise _malformed("non_finite_number", value=name)


def _to_bytes(data: str | bytes, max_bytes: int) -> bytes:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > max_bytes:
        raise _malformed("too_large", size_bytes=len(raw), max_bytes=max_bytes)
    return raw


def loads_json(data: str | bytes, *, max_bytes: int = MAX_DOCUMENT_BYTES) -> dict[str, Any]:
    raw = _to_bytes(data, max_bytes)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise _malformed("invalid_utf8") from None
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)
    except BluxError:
        raise
    except RecursionError:
        raise _malformed("nesting_too_deep") from None
    except json.JSONDecodeError as exc:
        raise _malformed("invalid_json", line=exc.lineno, column=exc.colno) from None
    if not isinstance(value, dict):
        raise _malformed("not_an_object", type=type(value).__name__)
    return value


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys."""


def _construct_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    out: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in out:
            raise _malformed("duplicate_key", key=str(key)[:128], line=key_node.start_mark.line + 1)
        out[key] = loader.construct_object(value_node, deep=deep)
    return out


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def loads_yaml(data: str | bytes, *, max_bytes: int = MAX_DOCUMENT_BYTES) -> dict[str, Any]:
    raw = _to_bytes(data, max_bytes)
    try:
        value = yaml.load(raw.decode("utf-8"), Loader=_StrictLoader)  # noqa: S506 - strict SafeLoader subclass
    except BluxError:
        raise
    except UnicodeDecodeError:
        raise _malformed("invalid_utf8") from None
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise _malformed("invalid_yaml", line=(mark.line + 1) if mark else None) from None
    if not isinstance(value, dict):
        raise _malformed("not_an_object", type=type(value).__name__)
    _check_json_compatible(value)
    return value


def _check_json_compatible(value: Any, path: str = "$") -> None:
    """YAML can express values JSON cannot (dates, non-string keys, NaN); contracts are JSON."""
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise _malformed("non_string_key", path=path)
            _check_json_compatible(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _check_json_compatible(v, f"{path}[{i}]")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise _malformed("non_finite_number", path=path)
    elif not (value is None or isinstance(value, (str, int, bool))):
        raise _malformed("unsupported_type", path=path, type=type(value).__name__)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def dumps_json(value: Any, *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
    return canonical_json(value)


def sha256_of(value: Any) -> str:
    """SHA-256 of canonical JSON for structured values, or of the raw bytes for bytes."""
    raw = value if isinstance(value, bytes) else canonical_json(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
