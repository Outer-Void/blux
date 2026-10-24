from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from blux_sdk import dot_blux
from blux_sdk.errors import BluxError, ErrorCode
from tests.conftest import ROOT

EXAMPLE = ROOT / "examples" / "project"


def test_repo_dot_blux_is_valid() -> None:
    project = dot_blux.load(ROOT)
    assert project.project_id == "blux"
    assert [c.capability_id for c in project.capabilities()] == ["echo.test"]


def test_example_project_is_valid() -> None:
    project = dot_blux.load(EXAMPLE)
    assert project.policy["default_effect"] == "deny"


def _copy(tmp_path: Path) -> Path:
    shutil.copytree(EXAMPLE / ".blux", tmp_path / ".blux")
    return tmp_path


def test_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(BluxError) as info:
        dot_blux.load(tmp_path)
    assert info.value.code is ErrorCode.INVALID_ENVELOPE


@pytest.mark.parametrize("name", ["manifest.yaml", "capabilities.yaml", "policy.yaml"])
def test_missing_file(tmp_path: Path, name: str) -> None:
    (_copy(tmp_path) / ".blux" / name).unlink()
    with pytest.raises(BluxError) as info:
        dot_blux.load(tmp_path)
    assert info.value.details["file"] == name


@pytest.mark.parametrize(
    ("name", "content", "code"),
    [
        ("manifest.yaml", "contract: blux.project\ncontract_version: '9.0'\n", ErrorCode.UNSUPPORTED_CONTRACT_VERSION),
        ("manifest.yaml", "contract: blux.project\ncontract_version: 1.0\n", ErrorCode.MALFORMED_CONTRACT_VERSION),
        (
            "policy.yaml",
            "contract: blux.capability-set\ncontract_version: '1.0'\ncapabilities: []\n",
            ErrorCode.INVALID_ENVELOPE,
        ),
        ("policy.yaml", "a: 1\na: 2\n", ErrorCode.MALFORMED_INPUT),
        ("capabilities.yaml", "- not\n- a mapping\n", ErrorCode.MALFORMED_INPUT),
    ],
)
def test_invalid_file(tmp_path: Path, name: str, content: str, code: ErrorCode) -> None:
    (_copy(tmp_path) / ".blux" / name).write_text(content)
    with pytest.raises(BluxError) as info:
        dot_blux.load(tmp_path)
    assert info.value.code is code
    assert info.value.details["file"] == name
