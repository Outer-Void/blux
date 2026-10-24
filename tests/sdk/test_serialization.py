"""Malformed-input handling for JSON and YAML."""

from __future__ import annotations

import pytest

from blux_sdk.errors import BluxError, ErrorCode
from blux_sdk.serialization import canonical_json, dumps_json, loads_json, loads_yaml, sha256_of


def _reason(fn, data) -> str:
    with pytest.raises(BluxError) as info:
        fn(data)
    assert info.value.code is ErrorCode.MALFORMED_INPUT
    return str(info.value.details["reason"])


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"", "invalid_json"),
        (b"{", "invalid_json"),
        (b"{'a': 1}", "invalid_json"),
        (b'{"a": 1} trailing', "invalid_json"),
        (b"[1, 2]", "not_an_object"),
        (b'"str"', "not_an_object"),
        (b"null", "not_an_object"),
        (b'{"a": 1, "a": 2}', "duplicate_key"),
        (b'{"a": {"b": 1, "b": 1}}', "duplicate_key"),
        (b'{"a": NaN}', "non_finite_number"),
        (b'{"a": Infinity}', "non_finite_number"),
        (b'{"a": -Infinity}', "non_finite_number"),
        (b"\xff\xfe{}", "invalid_utf8"),
        (b'{"a": "' + b"x" * (1024 * 1024) + b'"}', "too_large"),
        (b"[" * 100000 + b"]" * 100000, "nesting_too_deep"),
    ],
)
def test_malformed_json(data: bytes, reason: str) -> None:
    assert _reason(loads_json, data) == reason


def test_json_accepts_str_and_bytes() -> None:
    assert loads_json('{"a": 1}') == loads_json(b'{"a": 1}') == {"a": 1}


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        ("a: 1\na: 2\n", "duplicate_key"),
        ("- a\n- b\n", "not_an_object"),
        ("just text", "not_an_object"),
        ("a: [unclosed\n", "invalid_yaml"),
        ("when: 2026-10-24\n", "unsupported_type"),
        ("1: one\n", "non_string_key"),
        ("x: .nan\n", "non_finite_number"),
        ("x: !!python/object/apply:os.system ['true']\n", "invalid_yaml"),
    ],
)
def test_malformed_yaml(data: str, reason: str) -> None:
    assert _reason(loads_yaml, data) == reason


def test_yaml_unquoted_version_is_a_float_and_rejected_downstream() -> None:
    from blux_sdk.schemas import check_envelope

    doc = loads_yaml("contract: blux.policy\ncontract_version: 1.0\n")
    assert doc["contract_version"] == 1.0
    with pytest.raises(BluxError) as info:
        check_envelope(doc, "policy")
    assert info.value.code is ErrorCode.MALFORMED_CONTRACT_VERSION


def test_canonical_json_is_order_independent_and_compact() -> None:
    assert canonical_json({"b": 1, "a": [1, {"d": 2, "c": "é"}]}) == '{"a":[1,{"c":"é","d":2}],"b":1}'
    assert sha256_of({"a": 1, "b": 2}) == sha256_of({"b": 2, "a": 1})
    assert sha256_of(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_dumps_refuses_nan() -> None:
    with pytest.raises(ValueError):
        dumps_json({"a": float("nan")})
