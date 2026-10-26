"""Child-process environments and output redaction (Stage 13, slice 6; parity matrix BLH-006, BLH-009).

Children get an allow-list environment, never a scrubbed copy of BLUX's. This closes the gap characterized
in BLHermes, where AWS_SECRET_ACCESS_KEY and DATABASE_URL reached children (child_env_scrub golden).
Redaction extends the SDK's credential patterns with secret-named key/value pairs, so every
redaction-golden sample comes out at least as redacted as in BLHermes. Clean-room implementation.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping

from blux_runtime.subprocess_provider import ENV_ALLOW
from blux_sdk.model_api import scrub

CHILD_ALLOW = (*ENV_ALLOW, "LANGUAGE", "TERM", "TZ", "USER", "LOGNAME", "SHELL", "COLUMNS", "LINES", "NO_COLOR")
SECRET_KEY = r"(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret)"  # noqa: S105 - key names, not a secret
_QUOTED = re.compile(rf"([\"']?{SECRET_KEY}[\"']?\s*[:=]\s*)([\"'])(.*?)\2", re.I)
_BARE = re.compile(rf"(\b\w*{SECRET_KEY}\w*\s*[:=]\s*)([^\s\"',;}}]+)", re.I)


def child_env(
    base: Mapping[str, str] | None = None, *, extra: Mapping[str, str] | None = None, allow: Iterable[str] = CHILD_ALLOW
) -> dict[str, str]:
    src = os.environ if base is None else base
    env = {k: src[k] for k in allow if k in src}
    for k in list(src):
        if k.startswith("LC_"):
            env[k] = src[k]
    env.update(extra or {})
    return env


def redact(text: str) -> str:
    out = scrub(text)
    out = _QUOTED.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]{m.group(2)}", out)
    return _BARE.sub(lambda m: m.group(1) + ("[redacted]" if m.group(2) != "[redacted]" else m.group(2)), out)
