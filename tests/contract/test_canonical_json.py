from __future__ import annotations

import json

import pytest

from eva.contracts import (
    JSON_SAFE_INTEGER_MAX,
    MAX_CONTRACT_BYTES,
    DuplicateKeyError,
    canonical_json_bytes,
    decode_json_bytes,
    read_contract_bytes,
    require_canonical_bytes,
)


def test_canonical_json_has_sorted_keys_utf8_and_no_whitespace() -> None:
    payload = {"z": 1, "name": "Eva", "a": [True, None]}
    assert canonical_json_bytes(payload) == b'{"a":[true,null],"name":"Eva","z":1}'


@pytest.mark.parametrize(
    "wire, error",
    [
        (b'{"a":1,"a":2}', DuplicateKeyError),
        (b"\xef\xbb\xbf{}", ValueError),
        (b"\xff", ValueError),
        (b'{"number":NaN}', ValueError),
        (b'{"number":1.25}', ValueError),
        (b'{"number":9007199254740992}', ValueError),
        ('{"text":"e\\u0301"}'.encode(), ValueError),
        ('{"text":"\\ud800"}'.encode(), ValueError),
    ],
)
def test_parser_rejects_ambiguous_or_invalid_json(
    wire: bytes, error: type[Exception]
) -> None:
    with pytest.raises(error):
        decode_json_bytes(wire)


def test_size_guard_runs_before_json_or_contract_dispatch() -> None:
    wire = b'{"padding":"' + (b"a" * MAX_CONTRACT_BYTES) + b'"}'
    with pytest.raises(ValueError, match="exceeds"):
        read_contract_bytes(wire)


def test_canonical_reader_rejects_semantically_equal_noncanonical_bytes() -> None:
    with pytest.raises(ValueError, match="not EvaCanonicalJSON"):
        require_canonical_bytes(b'{ "a" : 1 }')


def test_canonical_output_is_idempotent() -> None:
    first = canonical_json_bytes({"b": {"d": 2, "c": 1}, "a": "Eva"})
    second = canonical_json_bytes(json.loads(first))
    assert second == first


def test_safe_integer_boundary_is_explicit() -> None:
    assert decode_json_bytes(f'{{"number":{JSON_SAFE_INTEGER_MAX}}}'.encode()) == {
        "number": JSON_SAFE_INTEGER_MAX
    }
    assert decode_json_bytes(f'{{"number":{-JSON_SAFE_INTEGER_MAX}}}'.encode()) == {
        "number": -JSON_SAFE_INTEGER_MAX
    }
