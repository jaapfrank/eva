"""Digest validation rejects whitespace without rejecting valid hex case."""
import pytest
from eva.contracts.canonical_json import is_hex_sha256

@pytest.mark.parametrize("space", [" ", "\t", "\n", "\r", "\v", "\f"])
@pytest.mark.parametrize("position", [0, 16, 31])
def test_digest_rejects_whitespace_in_64_character_input(space, position):
    value = "ab" * position + space * 2 + "ab" * (31 - position)
    assert not is_hex_sha256(value)

@pytest.mark.parametrize("value", ["ab" * 32, "AB" * 32, "aB" * 32, "0" * 64])
def test_digest_preserves_valid_case(value):
    assert is_hex_sha256(value)
