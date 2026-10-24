"""Built-in reference capabilities and the reference policy."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from blux_runtime.builtin import echo
from blux_runtime.registry import CapabilityRegistry


def builtin_registry() -> CapabilityRegistry:
    reg = CapabilityRegistry()
    reg.register(echo.DESCRIPTOR, echo.handle)
    return reg


def reference_policy() -> dict[str, Any]:
    """Allows exactly echo.test, nothing else. Used when no .blux/policy.yaml is supplied."""
    text = resources.files("blux_runtime.builtin").joinpath("reference_policy.json").read_text(encoding="utf-8")
    doc: dict[str, Any] = json.loads(text)
    return doc
