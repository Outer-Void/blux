"""The Stage 36 register covers every candidate with all ten points, and every cited evidence file exists."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTER = json.loads((ROOT / "training" / "specialists.json").read_text())
CANDIDATES = {
    "capability-routing",
    "risk-classification",
    "verdict-classification",
    "intent-classification",
    "tool-selection",
    "repair-ranking",
    "code-patch-ranking",
    "structured-planning-classification",
}
VERDICTS = {"INTEGRATED", "REJECTED_WITH_EVIDENCE", "BLOCKED_DATA", "COVERED_BY_ROUTING_STUDY"}


def test_every_candidate_is_registered_with_all_points() -> None:
    by_id = {c["id"]: c for c in REGISTER["candidates"]}
    assert set(by_id) == CANDIDATES
    for c in by_id.values():
        assert c["verdict"] in VERDICTS
        assert all(isinstance(c[k], str) and c[k] for k in REGISTER["items"]), c["id"]


def test_evidence_exists_and_nothing_is_claimed_integrated() -> None:
    for c in REGISTER["candidates"]:
        for rel in c["evidence"]:
            assert (ROOT / rel).exists(), (c["id"], rel)
        assert c["verdict"] != "INTEGRATED"  # Stage 36: no specialist met an acceptance threshold


def test_the_hard_gate_has_an_explicit_evidence_backed_rejection() -> None:
    assert any(c["verdict"] == "REJECTED_WITH_EVIDENCE" for c in REGISTER["candidates"])
