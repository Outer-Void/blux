"""Stage 11 hard gate: the BLHermes inventory and parity matrix are complete and consistent.

Runs without the fork. Tests that compare against the fork's files need a blhermes-agent checkout
($BLHERMES_SOURCE or ../blhermes-agent) and skip otherwise.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator

from tests.conftest import ROOT
from tests.migration.blhermes import GOLDEN, MIGRATION, files_at_inventory_commit, fork, git, inventory, matrix

CLASSES = {"REMOVE", "REPLACE", "MIGRATE", "TEMPORARY_COMPATIBILITY", "UPSTREAM_ATTRIBUTION"}
RETAINED = {"MIGRATE", "TEMPORARY_COMPATIBILITY"}
# The 24 reference kinds Stage 11 requires, as inventory categories.
REQUIRED_CATEGORIES = {
    "module",
    "package_id",
    "command",
    "env_var",
    "path",
    "url",
    "updater",
    "network_hook",
    "provider",
    "tool",
    "skill",
    "plugin",
    "mcp",
    "cron",
    "gateway",
    "tui",
    "session",
    "memory",
    "subagent",
    "approval",
    "credential",
    "termux",
    "android",
    "install",
}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]*[A-Za-z]{2,}")
needs_fork = pytest.mark.skipif(fork() is None, reason="needs a blhermes-agent checkout ($BLHERMES_SOURCE)")


def features() -> list[dict]:
    return list(matrix()["features"])


# inventory -------------------------------------------------------------------------------------------


def test_inventory_covers_every_required_reference_kind() -> None:
    inv = inventory()
    present = {i["category"] for i in inv["items"]}
    assert present >= REQUIRED_CATEGORIES, REQUIRED_CATEGORIES - present
    assert inv["source"]["commit"] and inv["upstream"]["license"] == "MIT"


def test_every_inventory_item_is_classified_by_a_known_rule() -> None:
    inv = inventory()
    rules = {r["id"] for r in inv["rules"]}
    for item in inv["items"]:
        assert item["classification"] in CLASSES, item["id"]
        assert item["rule"] in rules, item["id"]
        assert item["category"] in inv["categories"], item["id"]
        assert item["locations"], item["id"]


def test_every_upstream_network_hook_is_identified_and_owned() -> None:
    """Every network hook belongs to a matrix feature that declares a network dependency."""
    networked = {s for f in features() if f["network"]["dependency"] != "none" for s in f["subsystems"]}
    hooks = [i for i in inventory()["items"] if i["category"] == "network_hook"]
    assert hooks
    unowned = [h["id"] for h in hooks if not set(h["subsystems"]) & networked]
    assert unowned == []
    telemetry = [h for h in hooks if h["identifier"].endswith(":: telemetry-export")]
    assert telemetry and all(h["classification"] == "REMOVE" for h in telemetry)


def test_nous_services_and_feeds_are_classified_remove() -> None:
    items = inventory()["items"]
    nous = [
        i
        for i in items
        if i["category"] == "url" and re.search(r"(portal|inference-api|welcome-api)\.nousresearch", i["identifier"])
    ]
    assert nous and all(i["classification"] == "REMOVE" for i in nous)
    upstream_repo = [i for i in items if i["category"] == "url" and i["rule"] == "R-URL-UPSTREAM-REPO"]
    assert upstream_repo and all(i["classification"] == "UPSTREAM_ATTRIBUTION" for i in upstream_repo)


def test_no_personal_data_is_published() -> None:
    for path in [MIGRATION / "inventory.json", MIGRATION / "parity-matrix.json", *sorted(GOLDEN.glob("*.json"))]:
        found = {m for m in EMAIL.findall(path.read_text(encoding="utf-8")) if not m.endswith((".example", ".test"))}
        assert found == set(), f"{path.name}: {sorted(found)[:3]}"
    contributors = next(i for i in inventory()["items"] if i["id"].startswith("attribution:contributors/"))
    assert contributors["count"] > 0 and contributors["classification"] == "UPSTREAM_ATTRIBUTION"


# matrix ----------------------------------------------------------------------------------------------


def test_matrix_is_schema_valid_with_unique_ids() -> None:
    schema = json.loads((MIGRATION / "parity-matrix.schema.json").read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(matrix()), key=str)
    assert errors == [], errors[:3]
    ids = [f["id"] for f in features()]
    assert len(ids) == len(set(ids))


def test_every_inventory_subsystem_is_mapped_to_a_feature() -> None:
    mapped = {s for f in features() for s in f["subsystems"]}
    assert set(inventory()["subsystem_files"]) - mapped == set()
    assert mapped - set(inventory()["subsystem_files"]) == set()  # no feature names a subsystem that does not exist


def test_every_retained_feature_has_a_target_and_an_evidence_plan() -> None:
    for f in features():
        if f["classification"] in RETAINED:
            assert f["target"] and f["target"]["module"], f["id"]
            assert f["evidence_plan"].strip(), f["id"]
            assert f["tests"]["characterization"] or f["tests"]["upstream"], f["id"]
            assert f["provenance"]["requirement"], f["id"]
        if f["classification"] == "REMOVE":
            assert f["target"] is None and f["migration_state"] == "PLANNED_REMOVAL", f["id"]
        if f["migration_state"] in ("NOT_STARTED", "DEFERRED") and f["classification"] in RETAINED:
            assert f["blocker"] or f["evidence_plan"], f["id"]


def test_characterization_references_point_at_recorded_probes() -> None:
    from tests.migration.test_blhermes_characterization import PROBES

    for f in features():
        for ref in f["tests"]["characterization"]:
            name = re.fullmatch(
                r"tests/migration/test_blhermes_characterization\.py::test_probe_matches_golden\[(\w+)\]", ref
            )
            assert name and name.group(1) in PROBES, (f["id"], ref)
            assert (GOLDEN / f"{name.group(1)}.json").exists(), (f["id"], ref)
        if f["tests"]["characterization"]:
            assert f["migration_state"] == "CHARACTERIZED", f["id"]


def test_blux_sources_do_not_contact_removed_upstream_services() -> None:
    removed = re.compile(r"(portal|inference-api|welcome-api|hermes-agent)\.nousresearch\.com")
    for part in ("cli", "runtime", "sdk/python/src", "contracts"):
        for path in (ROOT / part).rglob("*"):
            if path.is_file() and path.suffix in (".py", ".json", ".toml"):
                assert not removed.search(path.read_text(encoding="utf-8", errors="replace")), path


# against the fork --------------------------------------------------------------------------------------


@needs_fork
def test_matrix_locations_and_upstream_tests_exist_in_the_fork() -> None:
    files = files_at_inventory_commit()
    dirs = {"/".join(f.split("/")[:i]) for f in files for i in range(1, f.count("/") + 1)}
    for f in features():
        for loc in f["source_locations"]:
            assert loc in files or loc in dirs, (f["id"], loc)
        for pattern in f["tests"]["upstream"]:
            assert any(fnmatch.fnmatch(p, pattern) for p in files), (f["id"], pattern)


@needs_fork
def test_nothing_was_deleted_from_the_fork() -> None:
    path = fork()
    assert path is not None
    commit = inventory()["source"]["commit"]
    assert subprocess.run(["git", "-C", str(path), "merge-base", "--is-ancestor", commit, "HEAD"]).returncode == 0
    assert git(path, "diff", "--diff-filter=D", "--name-only", commit, "HEAD").strip() == ""


@needs_fork
def test_inventory_is_reproducible() -> None:
    """Slow (minutes): a fresh scan of the inventoried commit reproduces inventory.json byte for byte."""
    path = fork()
    assert path is not None
    if git(path, "rev-parse", "HEAD").strip() != inventory()["source"]["commit"]:
        pytest.skip("the fork moved past the inventoried commit; re-inventory in a new stage")
    if not __import__("os").environ.get("BLHERMES_INVENTORY_CHECK"):
        pytest.skip("set BLHERMES_INVENTORY_CHECK=1 to rescan (takes minutes)")
    proc = subprocess.run(
        [sys.executable, str(MIGRATION / "inventory.py"), "--source", str(path), "--check"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
