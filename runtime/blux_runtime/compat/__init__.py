"""Temporary compatibility paths. Every entry is explicit, opt-in and expires (ADR-0013).

Registry: (id, module, expires, what it reads). tests/core/test_compat.py fails after an expiry date.
"""

from __future__ import annotations

import datetime as _dt

from blux_runtime.compat import hermes

REGISTRY: tuple[tuple[str, str, _dt.date, str], ...] = (
    (
        hermes.COMPAT_ID,
        "blux_runtime.compat.hermes",
        hermes.COMPAT_EXPIRES,
        "HERMES_HOME or ~/.hermes: state.db sessions/messages and .env provider keys (read-only, on request)",
    ),
)
