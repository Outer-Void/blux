"""Stage 37 requirement: no application-level dependency on one physical model.

Applications name logical models (blux-main, blux-fast, blux-reason, blux-code). Physical provider models live
only in the routing table, so a native specialist can be added as a route and rolled back by editing routes,
without touching code.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from blux_runtime.models.capability import default_routes
from blux_sdk import model_api

ROOT = Path(__file__).resolve().parents[2]
CODE_DIRS = ("runtime", "cli", "sdk/python/src", "training/blux_training", "evals")


def physical_models() -> set[str]:
    table = default_routes()
    return {r["provider_model"] for m in table["models"].values() for r in m["routes"]}


def tracked_python() -> list[Path]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--", *CODE_DIRS], capture_output=True, text=True, check=True)
    return [ROOT / p for p in out.stdout.split() if p.endswith(".py")]


def test_code_never_names_a_physical_model() -> None:
    models = physical_models()
    assert models
    offenders = [(p.relative_to(ROOT), m) for p in tracked_python() for m in models if m in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_every_logical_model_has_a_fallback_route() -> None:
    table = default_routes()
    for name in model_api.LOGICAL_MODELS:
        routes = table["models"][name]["routes"]
        assert len(routes) >= 2, f"{name} needs a fallback so a new (e.g. native) primary can be rolled back"
