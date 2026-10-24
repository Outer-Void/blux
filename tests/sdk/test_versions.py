from __future__ import annotations

import pytest

from blux_sdk.errors import BluxError, ErrorCode
from blux_sdk.versions import CONTRACT_VERSION, negotiate, parse_version, require_supported


@pytest.mark.parametrize(("value", "parsed"), [("1.0", (1, 0)), ("1.12", (1, 12)), ("0.9", (0, 9)), ("10.0", (10, 0))])
def test_parse(value: str, parsed: tuple[int, int]) -> None:
    assert parse_version(value) == parsed


@pytest.mark.parametrize(
    "value", ["1", "1.0.0", "v1.0", "01.0", "1.01", "1.", ".1", "", " 1.0", "1.0 ", 1, 1.0, None, True, [], {}]
)
def test_malformed_versions_are_explicit(value: object) -> None:
    with pytest.raises(BluxError) as info:
        parse_version(value)
    assert info.value.code is ErrorCode.MALFORMED_CONTRACT_VERSION


@pytest.mark.parametrize("value", ["0.9", "1.1", "2.0", "99.0"])
def test_unsupported_versions_are_explicit(value: str) -> None:
    with pytest.raises(BluxError) as info:
        require_supported(value)
    assert info.value.code is ErrorCode.UNSUPPORTED_CONTRACT_VERSION
    assert info.value.details == {"received": value, "supported": ["1.0"]}


def test_current_version_is_supported() -> None:
    assert require_supported(CONTRACT_VERSION) == "1.0"


def test_negotiate_picks_highest_common_numerically() -> None:
    assert negotiate(["1.0", "1.2", "1.10"], ["1.0", "1.2", "1.10", "2.0"]) == "1.10"
    assert negotiate(["1.0"]) == "1.0"


def test_negotiate_without_overlap_fails() -> None:
    with pytest.raises(BluxError) as info:
        negotiate(["2.0", "2.1"])
    assert info.value.code is ErrorCode.UNSUPPORTED_CONTRACT_VERSION
    assert info.value.details["offered"] == ["2.0", "2.1"]


def test_negotiate_rejects_malformed_offer() -> None:
    with pytest.raises(BluxError) as info:
        negotiate(["1.0", "one"])
    assert info.value.code is ErrorCode.MALFORMED_CONTRACT_VERSION
