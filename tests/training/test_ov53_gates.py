"""OV-53: the training data gates keep reasoning-model output, common personal data and reworded siblings of
protected evaluation items out of training data.

Each test failed on cfd7470:
- `<think>` (Qwen3, DeepSeek-R1), `reasoning`, `thought_process`, `thinking_blocks` and harmony analysis markers
  passed the hidden-reasoning gate;
- look-alike e-mail domains, most phone formats, IPv6, /root and Windows home paths, card numbers and SSNs passed
  the personal-data gate unredacted;
- a reworded sibling of a protected (holdout) item entered train, in the same build or in the next version.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from blux_training import gates, pipeline
from tests.training.test_pipeline import synthetic


@pytest.mark.parametrize(
    "trajectory",
    [
        {"final_result": "<think>the user wants</think>42"},
        {"final_result": "<|channel|>analysis<|message|>hidden"},
        {"reasoning": "summary of what the model thought"},
        {"reasoning_details": [{"text": "..."}]},
        {"thought_process": "..."},
        {"thinking_blocks": [{"text": "..."}]},
        {"steps": [{"Thought": "..."}]},
    ],
)
def test_reasoning_model_output_is_rejected(trajectory: dict[str, Any]) -> None:
    assert gates.hidden_reasoning(trajectory).rejected


@pytest.mark.parametrize(
    "trajectory",
    [
        {"reasoning_structure": {"steps": ["a", "b"]}},  # BLUX's explicit structure
        {"reflection": "CogA's explicit reflection field"},
        {"analysis": "a tool result field named analysis"},
        {"text": "We think <b>this</b> is fine; reasoning about it is allowed in prose."},
    ],
)
def test_explicit_structures_and_prose_are_kept(trajectory: dict[str, Any]) -> None:
    assert not gates.hidden_reasoning(trajectory).rejected


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("mail alice@notexample.com", "email"),
        ("call 555-123-4567", "phone"),
        ("call (555) 123-4567", "phone"),
        ("ring +44 20 7946 0958", "phone"),
        ("host fe80::1ff:fe23:4567:890a", "ipv6"),
        ("see /root/.ssh/config", "home-path"),
        (r"open C:\Users\Jadis\notes.txt", "home-path"),
        ("card 4111 1111 1111 1111", "card"),
        ("ssn 123-45-6789", "ssn"),
    ],
)
def test_common_personal_data_is_redacted(text: str, kind: str) -> None:
    out, result = gates.personal_data(text)
    assert result.redactions == [kind]
    assert out != text


@pytest.mark.parametrize(
    "text",
    [
        "mail bob@example.com or ops@mail.example.org",
        "xs[::2], xs[1::2], std::vector, dead::beef",
        "at 12:30:45 on 2026-11-07, MAC aa:bb:cc:dd:ee:ff",
        "doc addresses 2001:db8::1, 192.0.2.10, [::1]:443",
        "epoch 1730937600000, size 1048576, id 4111111111111112",
        r"C:\Users\Public\Desktop and /rootfs/etc",
    ],
)
def test_technical_text_is_not_redacted(text: str) -> None:
    out, result = gates.personal_data(text)
    assert (out, result.redactions) == (text, [])


def _write(directory: Path, sources: dict[str, dict[str, Any]]) -> Path:
    directory.mkdir()
    for name, doc in sources.items():
        (directory / name).write_text(json.dumps(doc))
    return directory


def _holdout_and_siblings(n: int) -> dict[str, dict[str, Any]]:
    held = synthetic("Order the release steps for a CLI tool: build the wheel, sign it, publish it, announce it.")
    held["split_hint"] = "holdout"
    out = {"held.json": held}
    for i in range(n):
        sib = copy.deepcopy(held)
        del sib["split_hint"]
        sib["trajectory"]["input"]["task"] += f" Variant {i}."
        out[f"sib{i}.json"] = sib
    return out


def test_siblings_of_a_protected_record_in_the_same_build_are_rejected(tmp_path: Path) -> None:
    sources = _holdout_and_siblings(5)
    sources["other.json"] = synthetic("Summarize the changelog for the 2.0 release in three bullet points.")
    result = pipeline.build(_write(tmp_path / "in", sources), tmp_path / "out", version="1")
    outcomes = {o["source"]: o for o in result["report"]["outcomes"]}
    assert outcomes["held.json"]["split"] == "holdout"
    assert all(outcomes[f"sib{i}.json"]["status"] == "rejected" for i in range(5))
    assert "protected record" in outcomes["sib0.json"]["reasons"][0]
    assert outcomes["other.json"]["status"] == "accepted"
    assert pipeline.validate(tmp_path / "out") == []


def test_siblings_of_an_earlier_versions_holdout_are_rejected(tmp_path: Path) -> None:
    v1 = {"held.json": _holdout_and_siblings(0)["held.json"]}
    pipeline.build(_write(tmp_path / "v1in", v1), tmp_path / "v1", version="1")
    siblings = {k: v for k, v in _holdout_and_siblings(5).items() if k != "held.json"}
    result = pipeline.build(
        _write(tmp_path / "v2in", siblings), tmp_path / "v2", version="2", lock_path=tmp_path / "v1" / "split-lock.json"
    )
    assert result["manifest"]["protected_sources"] == 1  # v1's holdout, found from the lock's dataset
    assert {o["status"] for o in result["report"]["outcomes"]} == {"rejected"}
    assert sum(result["manifest"]["counts"].values()) == 0
