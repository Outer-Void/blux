"""The Hook System v1 event catalog, read from the packaged contract (ADR-0012).

The single source of truth is `contracts/hooks/v1/events.schema.json`. `schemas/v1/` in this package is a
byte-identical copy (packaging/sync_schemas.py). Each event's `$defs` entry carries an `x-blux-hook`
annotation: family, phase, vetoable, the mutable fields and how they may change, the sensitive fields, and
which component may emit it.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator

HOOKS_VERSION = "1.0"
KINDS = {"event": "events", "receipt": "receipt", "registration": "registration"}
# How a mutable field may change. Every rule narrows; none can widen what the operation may do.
MUTATION_RULES = ("superset", "decrease")


@dataclass(frozen=True)
class EventSpec:
    name: str
    family: str
    phase: str  # "before", "after" or "notify"
    vetoable: bool
    mutable: dict[str, str]  # field -> rule in MUTATION_RULES
    sensitive: tuple[str, ...]
    emitters: str
    summary: str
    fields: tuple[str, ...]
    required: tuple[str, ...]


@functools.lru_cache(maxsize=1)
def schemas() -> dict[str, dict[str, Any]]:
    root = resources.files("blux_runtime.hooks").joinpath("schemas").joinpath("v1")
    return {
        e.name.removesuffix(".schema.json"): json.loads(e.read_text(encoding="utf-8"))
        for e in root.iterdir()
        if e.name.endswith(".schema.json")
    }


@functools.lru_cache(maxsize=1)
def catalog() -> dict[str, EventSpec]:
    out: dict[str, EventSpec] = {}
    for definition in schemas()["events"]["$defs"].values():
        meta = definition["x-blux-hook"]
        out[meta["name"]] = EventSpec(
            name=meta["name"],
            family=meta["family"],
            phase=meta["phase"],
            vetoable=bool(meta["vetoable"]),
            mutable=dict(meta["mutable"]),
            sensitive=tuple(meta["sensitive"]),
            emitters=meta["emitters"],
            summary=meta["summary"],
            fields=tuple(definition["properties"]),
            required=tuple(definition["required"]),
        )
    return dict(sorted(out.items()))


def spec(name: str) -> EventSpec:
    try:
        return catalog()[name]
    except KeyError:
        raise KeyError(f"unknown hook event {name!r}") from None


@functools.cache
def _validator(kind: str) -> Draft202012Validator:
    return Draft202012Validator(schemas()[KINDS[kind]])


def schema_errors(kind: str, doc: Any) -> list[str]:
    errors = sorted(_validator(kind).iter_errors(doc), key=lambda e: (list(map(str, e.absolute_path)), e.validator))
    return [f"$/{'/'.join(map(str, e.absolute_path))}: {e.validator}" for e in errors[:20]]
