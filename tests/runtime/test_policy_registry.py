from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from blux_runtime.builtin import builtin_registry, echo, reference_policy
from blux_runtime.policy import PolicyEngine
from blux_runtime.registry import CapabilityRegistry, ModelRegistry
from blux_sdk.capability import CapabilityDescriptor
from blux_sdk.errors import BluxError, ErrorCode
from blux_sdk.serialization import loads_yaml
from tests.conftest import ROOT, load_fixture

USER = {"type": "user", "id": "u"}
AGENT = {"type": "agent", "id": "a"}


def _cap(capability_id: str, side_effects: str = "none", perms: tuple[str, ...] = ()) -> CapabilityDescriptor:
    doc = copy.deepcopy(echo.DESCRIPTOR)
    doc.update(capability_id=capability_id, side_effects=side_effects, required_permissions=list(perms))
    return CapabilityDescriptor.from_document(doc)


def _policy(*rules: dict[str, Any]) -> PolicyEngine:
    return PolicyEngine({**reference_policy(), "policy_id": "t", "rules": list(rules)})


def test_default_deny() -> None:
    d = _policy().evaluate(_cap("echo.test"), USER)
    assert d == {
        "effect": "deny",
        "policy_id": "t",
        "rule_id": None,
        "reason": "no applicable allow rule (default deny)",
    }


def test_glob_allow_and_deny_wins_regardless_of_order() -> None:
    allow = {"rule_id": "a", "effect": "allow", "capabilities": ["fs.*"]}
    deny = {"rule_id": "d", "effect": "deny", "capabilities": ["fs.read"], "actor_types": ["agent"]}
    for engine in (_policy(allow, deny), _policy(deny, allow)):
        assert engine.evaluate(_cap("fs.read", "read"), USER)["effect"] == "allow"
        assert engine.evaluate(_cap("fs.read", "read"), AGENT) == {
            "effect": "deny",
            "policy_id": "t",
            "rule_id": "d",
            "reason": "denied by rule",
        }


def test_glob_does_not_match_prefix_of_other_namespace() -> None:
    engine = _policy({"rule_id": "a", "effect": "allow", "capabilities": ["fs.*"]})
    assert engine.evaluate(_cap("fsx.read"), USER)["effect"] == "deny"


def test_max_side_effects_ceiling() -> None:
    engine = _policy({"rule_id": "a", "effect": "allow", "capabilities": ["fs.*"], "max_side_effects": "read"})
    assert engine.evaluate(_cap("fs.read", "read"), USER)["effect"] == "allow"
    assert engine.evaluate(_cap("fs.write", "write"), USER)["effect"] == "deny"


def test_destructive_requires_exact_id() -> None:
    glob = _policy({"rule_id": "g", "effect": "allow", "capabilities": ["device.*"]})
    exact = _policy({"rule_id": "e", "effect": "allow", "capabilities": ["device.wipe"]})
    cap = _cap("device.wipe", "destructive")
    assert glob.evaluate(cap, USER)["effect"] == "deny"
    assert exact.evaluate(cap, USER) == {
        "effect": "allow",
        "policy_id": "t",
        "rule_id": "e",
        "reason": "allowed by rule",
    }


def test_required_permissions_must_be_granted() -> None:
    cap = _cap("notes.search", "read", ("fs:read:notes",))
    assert (
        _policy({"rule_id": "a", "effect": "allow", "capabilities": ["notes.*"]}).evaluate(cap, USER)["effect"]
        == "deny"
    )
    granted = _policy({"rule_id": "a", "effect": "allow", "capabilities": ["notes.*"], "grants": ["fs:read:notes"]})
    assert granted.evaluate(cap, USER)["effect"] == "allow"


def test_actor_claim_cannot_self_authorize() -> None:
    """Only the policy document grants authority; an actor id like 'root' changes nothing."""
    engine = _policy({"rule_id": "a", "effect": "allow", "capabilities": ["x.y"], "actor_types": ["user"]})
    assert engine.evaluate(_cap("x.y"), {"type": "agent", "id": "root"})["effect"] == "deny"


def test_example_project_policy() -> None:
    from blux_sdk import dot_blux

    project = dot_blux.load(ROOT / "examples" / "project")
    engine = PolicyEngine(project.policy)
    cap = project.capabilities()[0]
    assert engine.evaluate(cap, USER)["rule_id"] == "allow-users-search"
    assert engine.evaluate(cap, AGENT)["rule_id"] == "deny-agents-everything-but-read"


def test_registry_rejects_duplicates_and_bad_descriptors() -> None:
    reg = builtin_registry()
    with pytest.raises(ValueError):
        reg.register(echo.DESCRIPTOR, echo.handle)
    with pytest.raises(BluxError) as info:
        CapabilityRegistry().register({**echo.DESCRIPTOR, "side_effects": "maybe"}, echo.handle)
    assert info.value.code is ErrorCode.SCHEMA_VALIDATION_FAILED
    with pytest.raises(TypeError):
        CapabilityRegistry().register(echo.DESCRIPTOR, "not callable")  # type: ignore[arg-type]
    with pytest.raises(BluxError) as info:
        reg.get("missing.cap")
    assert info.value.code is ErrorCode.UNKNOWN_CAPABILITY


@pytest.mark.parametrize("path", [ROOT / ".blux" / "capabilities.yaml", ROOT / "registry" / "capabilities.yaml"])
def test_published_capability_sets_match_runtime(path: Path) -> None:
    assert loads_yaml(path.read_bytes()) == builtin_registry().to_capability_set()


def test_repo_policy_matches_reference_policy() -> None:
    assert loads_yaml((ROOT / ".blux" / "policy.yaml").read_bytes()) == reference_policy()


class _Model:
    def __init__(self, doc: dict[str, Any]) -> None:
        self.doc = doc

    def describe(self) -> dict[str, Any]:
        return self.doc


def test_model_registry() -> None:
    reg = ModelRegistry()
    doc = load_fixture("valid", "model", "local.json")
    assert reg.register(_Model(doc)) == "blux/echo-model"
    assert reg.describe("blux/echo-model") == doc
    with pytest.raises(ValueError):
        reg.register(_Model(doc))
    with pytest.raises(BluxError):
        reg.register(_Model({**doc, "model_id": "other/x"}))
