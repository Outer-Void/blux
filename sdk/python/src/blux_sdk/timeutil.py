"""RFC 3339 UTC timestamps as used by every contract (always Z, millisecond precision when generated)."""

from __future__ import annotations

import datetime as _dt
import re

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?Z$")


def utc_now() -> str:
    return format_timestamp(_dt.datetime.now(_dt.timezone.utc))


def format_timestamp(value: _dt.datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    value = value.astimezone(_dt.timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def parse_timestamp(value: str) -> _dt.datetime:
    m = _TS_RE.match(value)
    if not m:
        raise ValueError("not an RFC 3339 UTC timestamp")
    base = _dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=_dt.timezone.utc)
    frac = (m.group(2) or "0").ljust(9, "0")
    return base + _dt.timedelta(microseconds=int(frac) // 1000)
