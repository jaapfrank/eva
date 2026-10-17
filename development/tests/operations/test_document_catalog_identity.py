"""Keep historical catalog pins meaningful across restore state transitions."""
from copy import deepcopy
import pytest
from eva.contracts.canonical_json import canonical_sha256
from tools.installation_document_catalog import canonical_document_catalog


def test_gateway_snapshot_order_does_not_rewrite_expected_hash_or_input():
    rows = [{"name": "z", "inputSchema": {"type": "object"}}, {"name": "a", "description": "read"}]
    original = deepcopy(rows)
    expected = canonical_sha256([rows[1], rows[0]])
    result = canonical_document_catalog(rows, {"catalog_gateway_id": "saved"})
    assert canonical_sha256(result) == expected
    assert rows == original
    assert canonical_document_catalog(rows, {}) == original
    assert canonical_sha256(canonical_document_catalog(rows, {})) != expected


@pytest.mark.parametrize("rows", [None, [{"name": "a"}, {"name": "a"}], [{}], [None], [{"name": 1}]])
def test_gateway_catalog_ambiguous_identity_is_rejected(rows):
    with pytest.raises(ValueError):
        canonical_document_catalog(rows, {"catalog_gateway_id": "saved"})
