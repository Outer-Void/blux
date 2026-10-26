"""Skills: instruction documents with declared permissions (Stage 14; parity matrix BLH-015).

A skill is a directory holding SKILL.md (YAML front matter plus Markdown). BLUX reads the BLHermes format
(name, description, version, license, platforms, prerequisites.commands) unchanged, and adds BLUX
metadata under `metadata.blux`:

    metadata:
      blux:
        capabilities: [shell.exec]        # capabilities the skill's instructions may use
        hooks:                            # optional Hook System v1 subscriptions (via adapters only)
          events: [tool.before_execute]
          permissions: [veto:tool.before_execute]

Rules:
- A skill without a `license` is not loaded. Licence review is part of admission.
- Skills grant nothing. Their declared capabilities still go through policy, and the operator's
  HookPolicy decides their hook grants.
- Skills run no code at load time. Instructions are returned secret-scrubbed through `skill.list`
  and `skill.get` (read-only capabilities).

Clean-room implementation; no upstream code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from blux_runtime.interfaces import ExecutionContext
from blux_runtime.registry import CapabilityRegistry
from blux_runtime.security import redact
from blux_sdk.errors import BluxError, ErrorCode

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_SKILL_BYTES = 256 * 1024
PROVIDER = {"repository": "Outer-Void/blux", "kind": "builtin"}


class SkillError(ValueError):
    pass


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    version: str
    license: str
    path: Path
    instructions: str
    platforms: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    hooks: dict[str, Any] = field(default_factory=dict)
    legacy_metadata: bool = False

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "license": self.license,
            "platforms": list(self.platforms),
            "prerequisites": list(self.prerequisites),
            "capabilities": list(self.capabilities),
            "hooks": bool(self.hooks),
        }


def parse(path: Path) -> Skill:
    raw = path.read_bytes()
    if len(raw) > MAX_SKILL_BYTES:
        raise SkillError(f"{path}: larger than {MAX_SKILL_BYTES} bytes")
    text = raw.decode("utf-8")
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        raise SkillError(f"{path}: no YAML front matter")
    meta = yaml.safe_load(m.group(1)) or {}
    if not isinstance(meta, dict):
        raise SkillError(f"{path}: front matter must be a mapping")
    name = str(meta.get("name", ""))
    if not NAME.match(name):
        raise SkillError(f"{path}: invalid skill name {name!r}")
    if not meta.get("license"):
        raise SkillError(f"{path}: no license declared; skills are admitted only after licence review")
    metadata = meta.get("metadata") or {}
    blux = metadata.get("blux") or {}
    prereq = (meta.get("prerequisites") or {}).get("commands") or []
    return Skill(
        name=name,
        description=str(meta.get("description", ""))[:1024],
        version=str(meta.get("version", "0")),
        license=str(meta["license"]),
        path=path.parent,
        instructions=m.group(2),
        platforms=tuple(str(p) for p in meta.get("platforms") or ()),
        prerequisites=tuple(str(c) for c in prereq),
        capabilities=tuple(str(c) for c in blux.get("capabilities") or ()),
        hooks=dict(blux.get("hooks") or {}),
        legacy_metadata="hermes" in metadata,
    )


class SkillLibrary:
    def __init__(self, roots: list[Path]) -> None:
        self.skills: dict[str, Skill] = {}
        self.rejected: dict[str, str] = {}
        for root in roots:
            for skill_md in sorted(Path(root).glob("**/SKILL.md")):
                try:
                    skill = parse(skill_md)
                except (SkillError, UnicodeDecodeError, yaml.YAMLError) as exc:
                    self.rejected[str(skill_md)] = str(exc)[:256]
                    continue
                if skill.name in self.skills:
                    self.rejected[str(skill_md)] = f"duplicate skill name {skill.name!r}"
                    continue
                self.skills[skill.name] = skill

    def register(self, registry: CapabilityRegistry) -> None:
        def desc(cid: str, summary: str, inp: dict[str, Any], out: dict[str, Any]) -> dict[str, Any]:
            return {
                "contract": "blux.capability",
                "contract_version": "1.0",
                "capability_id": cid,
                "version": "1.0.0",
                "summary": summary,
                "contract_versions": ["1.0"],
                "input_schema": inp,
                "output_schema": out,
                "side_effects": "read",
                "required_permissions": [],
                "idempotent": True,
                "timeout_ms": 5000,
                "provider": {**PROVIDER, "entrypoint": f"blux_runtime.extensions.skills:{cid}"},
                "labels": {"kind": "skill"},
            }

        def list_skills(input: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:  # noqa: A002
            return {"skills": [s.summary() for s in self.skills.values()], "rejected": len(self.rejected)}

        def get_skill(input: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:  # noqa: A002
            skill = self.skills.get(input["name"])
            if skill is None:
                raise BluxError(ErrorCode.EXECUTION_FAILED, "No such skill.", details={"reason": "not_found"})
            return {**skill.summary(), "instructions": redact(skill.instructions)}

        registry.register(
            desc(
                "skill.list",
                "List admitted skills.",
                {"type": "object", "additionalProperties": False},
                {"type": "object"},
            ),
            list_skills,
        )
        registry.register(
            desc(
                "skill.get",
                "Get a skill's instructions (secret-scrubbed).",
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                },
                {"type": "object"},
            ),
            get_skill,
        )
