from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from blux_runtime.builtin import builtin_registry, reference_policy
from blux_runtime.executor import InMemoryReceiptSink, Runtime

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "v1"


def load_fixture(*parts: str) -> Any:
    return json.loads((FIXTURES.joinpath(*parts)).read_text(encoding="utf-8"))


def fixture_files(group: str) -> list[Path]:
    return sorted((FIXTURES / group).glob("*/*.json"))


@pytest.fixture
def sink() -> InMemoryReceiptSink:
    return InMemoryReceiptSink()


@pytest.fixture
def runtime(sink: InMemoryReceiptSink) -> Runtime:
    return Runtime(builtin_registry(), reference_policy(), receipt_sink=sink)


@pytest.fixture
def echo_request() -> dict[str, Any]:
    from blux_sdk import envelopes

    return envelopes.execution_request("echo.test", {"message": "hello"}, actor={"type": "user", "id": "tester"})
