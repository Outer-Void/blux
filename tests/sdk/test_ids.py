from __future__ import annotations

import time

import pytest

from blux_sdk import ids


@pytest.mark.parametrize(
    ("kind", "prefix"),
    [
        (ids.IdKind.OPERATION, "op_"),
        (ids.IdKind.RECEIPT, "rcpt_"),
        (ids.IdKind.ARTIFACT, "art_"),
        (ids.IdKind.SESSION, "ses_"),
    ],
)
def test_generated_ids_have_prefix_and_validate(kind: ids.IdKind, prefix: str) -> None:
    value = ids.new_id(kind)
    assert value.startswith(prefix)
    assert len(value) == len(prefix) + 26
    assert ids.is_valid(kind, value)


def test_ids_are_unique() -> None:
    assert len({ids.new_operation_id() for _ in range(5000)}) == 5000


def test_ids_embed_creation_time_and_sort() -> None:
    before = int(time.time() * 1000)
    value = ids.new_operation_id()
    after = int(time.time() * 1000)
    assert before <= ids.timestamp_ms_of(value) <= after
    assert ids.ulid(1000) < ids.ulid(2000)


def test_ulid_bounds() -> None:
    assert ids.ulid(0).startswith("0000000000")
    assert ids.ulid(2**48 - 1).startswith("7ZZZZZZZZZ")
    with pytest.raises(ValueError):
        ids.ulid(2**48)
    with pytest.raises(ValueError):
        ids.ulid(-1)


@pytest.mark.parametrize("kind", [ids.IdKind.PROJECT, ids.IdKind.CAPABILITY, ids.IdKind.MODEL])
def test_declared_ids_cannot_be_generated(kind: ids.IdKind) -> None:
    with pytest.raises(ValueError):
        ids.new_id(kind)


@pytest.mark.parametrize(
    ("kind", "value", "ok"),
    [
        (ids.IdKind.PROJECT, "blux", True),
        (ids.IdKind.PROJECT, "void-os", True),
        (ids.IdKind.PROJECT, "Void", False),
        (ids.IdKind.PROJECT, "x", False),
        (ids.IdKind.PROJECT, "ends-", False),
        (ids.IdKind.CAPABILITY, "echo.test", True),
        (ids.IdKind.CAPABILITY, "echo", False),
        (ids.IdKind.CAPABILITY, "echo..test", False),
        (ids.IdKind.CAPABILITY, "e." + "a" * 200, False),
        (ids.IdKind.MODEL, "openai/gpt-x", True),
        (ids.IdKind.MODEL, "gpt-x", False),
        (ids.IdKind.OPERATION, "op_01JAAAAAAAAAAAAAAAAAAAAAAU", False),  # U is not Crockford
        (ids.IdKind.OPERATION, 123, False),
        (ids.IdKind.PARENT_OPERATION, "op_01JAAAAAAAAAAAAAAAAAAAAAAA", True),
    ],
)
def test_validation(kind: ids.IdKind, value: object, ok: bool) -> None:
    assert ids.is_valid(kind, value) is ok


def test_timestamp_of_rejects_declared_ids() -> None:
    with pytest.raises(ValueError):
        ids.timestamp_ms_of("echo.test")
