from __future__ import annotations

import eva.operations.model_credentials as credentials


def test_publisher_default_contract_matches_the_conversation_lease() -> None:
    assert credentials.MODEL_LEASE_OWNER_UID == 1000
    assert credentials.EVA_CORE_LEASE_READER_GID == 65532
    assert credentials.EVA_CORE_LEASE_DIRECTORY_MODE == 0o2750
    assert credentials.EVA_CORE_LEASE_FILE_MODE == 0o640
    assert credentials.CODEX_LEASE_READER_GID == 1000
    assert credentials.CODEX_LEASE_DIRECTORY_MODE == 0o700
    assert credentials.CODEX_LEASE_FILE_MODE == 0o600
