"""Policy evaluation (ADR-0007): default deny, deny wins, destructive needs an exact-id allow.

A request's actor is an input to policy, never a grant. Authority comes only from the policy document
the runtime was started with.
"""

from __future__ import annotations

from typing import Any

from blux_sdk.capability import CapabilityDescriptor, side_effect_rank
from blux_sdk.schemas import check_envelope


def _pattern_matches(pattern: str, capability_id: str) -> tuple[bool, bool]:
    """Return (matches, exact)."""
    if pattern.endswith(".*"):
        return capability_id.startswith(pattern[:-1]), False
    return pattern == capability_id, True


class PolicyEngine:
    def __init__(self, policy: dict[str, Any]) -> None:
        self.policy = check_envelope(policy, "policy")
        self.policy_id: str = self.policy["policy_id"]

    def evaluate(self, capability: CapabilityDescriptor, actor: dict[str, str]) -> dict[str, Any]:
        allow: dict[str, Any] | None = None
        for rule in self.policy["rules"]:
            matched_exact = None
            for pattern in rule["capabilities"]:
                matches, exact = _pattern_matches(pattern, capability.capability_id)
                if matches:
                    matched_exact = bool(matched_exact) or exact
            if matched_exact is None:
                continue
            if "actor_types" in rule and actor["type"] not in rule["actor_types"]:
                continue
            if rule["effect"] == "deny":
                return self._decision("deny", rule["rule_id"], rule.get("reason", "denied by rule"))
            if allow is not None:
                continue
            if "max_side_effects" in rule and side_effect_rank(capability.side_effects) > side_effect_rank(
                rule["max_side_effects"]
            ):
                continue
            if capability.side_effects == "destructive" and not matched_exact:
                continue
            if not set(capability.required_permissions) <= set(rule.get("grants", ())):
                continue
            allow = rule
        if allow is not None:
            return self._decision("allow", allow["rule_id"], allow.get("reason", "allowed by rule"))
        return self._decision("deny", None, "no applicable allow rule (default deny)")

    def _decision(self, effect: str, rule_id: str | None, reason: str) -> dict[str, Any]:
        return {"effect": effect, "policy_id": self.policy_id, "rule_id": rule_id, "reason": reason[:512]}


NOT_EVALUATED: dict[str, Any] = {
    "effect": "not_evaluated",
    "policy_id": None,
    "rule_id": None,
    "reason": "request rejected before policy evaluation",
}
