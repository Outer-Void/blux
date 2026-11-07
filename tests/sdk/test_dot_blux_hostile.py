"""OV-51: a hostile or broken .blux/ must become a MALFORMED_INPUT, never a hang, a crash or a read outside it.

GOD and the BLUX runtime read every project under a root through this loader, so one bad project used to stop
discovery of all of them. Each test failed on 4163637:
- a named pipe as capabilities.yaml blocked the scan forever;
- a symbolic link was followed out of .blux/;
- deeply nested YAML raised RecursionError;
- a 350-byte YAML alias bomb took 24 s (each further level costs 9x).
"""

from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from blux_sdk import dot_blux
from blux_sdk.errors import BluxError, ErrorCode
from blux_sdk.serialization import MAX_DOCUMENT_BYTES, loads_yaml
from tests.conftest import ROOT

EXAMPLE = ROOT / "examples" / "project"


def _copy(tmp_path: Path) -> Path:
    shutil.copytree(EXAMPLE / ".blux", tmp_path / ".blux")
    return tmp_path


def _within(seconds: float, fn: Callable[[], Any]) -> BaseException | Any:
    """Run fn in a daemon thread; fail if it does not finish in time (the old code never returned)."""
    box: list[Any] = []

    def target() -> None:
        try:
            box.append(fn())
        except BaseException as exc:  # returned to the test
            box.append(exc)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(seconds)
    assert box, f"did not finish within {seconds} s"
    return box[0]


def _malformed(result: Any) -> dict[str, Any]:
    assert isinstance(result, BluxError), result
    assert result.code is ErrorCode.MALFORMED_INPUT
    return result.details


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_a_named_pipe_is_refused_without_blocking(tmp_path: Path) -> None:
    caps = _copy(tmp_path) / ".blux" / "capabilities.yaml"
    caps.unlink()
    os.mkfifo(caps)
    details = _malformed(_within(5, lambda: dot_blux.load(tmp_path)))
    assert details["reason"] == "not_a_regular_file" and details["file"] == "capabilities.yaml"


def test_a_symbolic_link_out_of_dot_blux_is_refused(tmp_path: Path) -> None:
    project = _copy(tmp_path / "p")
    outside = tmp_path / "elsewhere.yaml"
    policy = project / ".blux" / "policy.yaml"
    shutil.move(policy, outside)  # a valid policy, just not inside .blux/
    os.symlink(outside, policy)
    details = _malformed(_within(5, lambda: dot_blux.load(project)))
    assert details["reason"] == "not_a_regular_file" and details["file"] == "policy.yaml"


def test_a_directory_in_place_of_a_file_is_refused(tmp_path: Path) -> None:
    caps = _copy(tmp_path) / ".blux" / "capabilities.yaml"
    caps.unlink()
    caps.mkdir()
    assert _malformed(_within(5, lambda: dot_blux.load(tmp_path)))["reason"] == "not_a_regular_file"


def test_reads_stop_at_the_size_limit(tmp_path: Path) -> None:
    big = tmp_path / "big.yaml"
    big.write_bytes(b"#" * (3 * MAX_DOCUMENT_BYTES))
    assert len(dot_blux.read_bounded(big)) == MAX_DOCUMENT_BYTES + 1
    manifest = _copy(tmp_path / "p") / ".blux" / "manifest.yaml"
    shutil.copy(big, manifest)
    assert _malformed(_within(5, lambda: dot_blux.load(tmp_path / "p")))["reason"] == "too_large"


def test_deep_nesting_is_malformed_not_a_crash(tmp_path: Path) -> None:
    manifest = _copy(tmp_path) / ".blux" / "manifest.yaml"
    manifest.write_text("[" * 100_000)
    assert _malformed(_within(30, lambda: dot_blux.load(tmp_path)))["reason"] == "nesting_too_deep"


def _bomb(levels: int) -> str:
    lines = ["a0: &a0 [x, x, x, x, x, x, x, x, x]"]
    lines += [f"a{i}: &a{i} [" + ", ".join([f"*a{i - 1}"] * 9) + "]" for i in range(1, levels)]
    return "\n".join(lines) + "\n"


def test_yaml_alias_expansion_is_bounded() -> None:
    doc = _bomb(10)  # about 450 bytes that expand to 9**10 (3.5 billion) values
    assert len(doc) < 1000
    details = _malformed(_within(10, lambda: loads_yaml(doc)))
    assert details["reason"] == "too_large" and details["expands_to_more_than"] == MAX_DOCUMENT_BYTES


def test_small_aliases_and_large_alias_free_documents_still_load() -> None:
    assert loads_yaml("base: &b {k: 1}\nother: *b\n") == {"base": {"k": 1}, "other": {"k": 1}}
    values = 250_000  # 1 MB limit, about 4 bytes per value
    doc = "x:\n" + "- 0\n" * values
    assert len(doc.encode()) < MAX_DOCUMENT_BYTES
    assert len(_within(30, lambda: loads_yaml(doc))["x"]) == values
