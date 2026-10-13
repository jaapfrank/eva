from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from eva.contracts.delegated_work import (
    DelegatedWorkGrant,
    DelegatedWorkResult,
    DelegatedWorkSpec,
    DelegatedWorkUsage,
)
from eva.contracts.lifecycle import AttentionWake, AttentionWakeV2


ROOT = Path(__file__).resolve().parents[3]
CORPUS = json.loads(
    (ROOT / "deploy/schemas/contracts/contract-corpus-v1.json").read_bytes()
)


def _accepted(contract: str) -> dict[str, object]:
    return next(
        json.loads(case["wireUtf8"])
        for case in CORPUS["cases"]
        if case["expect"] == "accept" and case["contract"] == contract
    )


def test_attention_v2_adds_only_the_delegated_work_kind() -> None:
    v1_kinds = AttentionWake.model_json_schema()["properties"]["kind"]["enum"]
    v2_kinds = AttentionWakeV2.model_json_schema()["properties"]["kind"]["enum"]

    assert set(v2_kinds) == {*v1_kinds, "delegated_work"}
    assert hashlib.sha256(
        (ROOT / "deploy/schemas/contracts/AttentionWake-v1.schema.json").read_bytes()
    ).hexdigest() == "79d91bec82cf0c2f18fe3ed6c2bf95d714842a2ac499a2d1ea6aca11bc5a7cbc"
    AttentionWakeV2.model_validate(_accepted("AttentionWake/v2"))


def test_delegated_work_grant_cannot_gain_ambient_authority() -> None:
    valid = _accepted("DelegatedWorkGrant/v1")
    DelegatedWorkGrant.model_validate(valid)

    for field, value in (
        ("context_refs", ["memory:private:001"]),
        ("capability_ids", ["capability:web:001"]),
        ("workspace_ref", "workspace:001"),
        ("child_network_access", True),
    ):
        changed = deepcopy(valid)
        changed[field] = value
        with pytest.raises(ValidationError):
            DelegatedWorkGrant.model_validate(changed)


def test_delegated_work_spec_and_usage_keep_deadline_and_unknowns_honest() -> None:
    work = _accepted("DelegatedWork/v1")
    DelegatedWorkSpec.model_validate(work)
    work["deadline"] = work["created_at"]
    with pytest.raises(ValidationError, match="deadline"):
        DelegatedWorkSpec.model_validate(work)

    usage = _accepted("DelegatedWorkUsage/v1")
    DelegatedWorkUsage.model_validate(usage)
    usage["measurement"] = "unmeasured"
    with pytest.raises(ValidationError, match="unmeasured"):
        DelegatedWorkUsage.model_validate(usage)


def test_delegated_work_result_binds_exact_output_bytes() -> None:
    result = _accepted("DelegatedWorkResult/v1")
    DelegatedWorkResult.model_validate(result)
    result["output"] = "gewijzigd"
    with pytest.raises(ValidationError, match="output hash differs"):
        DelegatedWorkResult.model_validate(result)
