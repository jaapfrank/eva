"""Admission tests only until the real atomic provider interface is available."""

from dataclasses import replace
import json

import pytest

from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.adapters.postgres.durable_goals import PostgresDurableGoalStore
from eva.application.conversation import ConversationConflict
from eva.application.durable_goals import DurableGoalError
from eva.providers.document_goal_plan import DocumentGoalPlan
from eva.providers.document_goals import DocumentGoalAuthority
from eva.providers.mcp_owner_authority import McpOwnerAuthority
from tests.postgres.test_durable_goal_store import spec
from tests.postgres.test_operational_timeline import timeline_engine  # noqa: F401


def test_real_owner_authority_is_fresh_and_plan_binding_survives_recreation(
    timeline_engine, private_tmp_path,
):
    mandate_file = private_tmp_path / "mandate.json"
    plan_file = private_tmp_path / "plan.json"
    mandate = {
        "contract": "OwnerConnectorMandate/v1", "mandate_id": "document-plan-authority",
        "tenant_id": "document-test", "installation_id": "isolated",
        "owner_principal_id": "owner", "issued_at": "2020-01-01T00:00:00Z",
        "authorization_source": "isolated operator fixture", "enabled": True,
        "public_https": False, "local_networks": [],
    }
    mandate_file.write_text(json.dumps(mandate))
    mandate_file.chmod(0o600)
    authority = McpOwnerAuthority(timeline_engine, str(mandate_file))
    goal = replace(spec("document-admission"), owner_id="owner", followup_instruction="Use draft")
    plan = DocumentGoalPlan(
        goal.sha256, "owner", str(authority.scope.tenant_id), str(authority.scope.installation_id),
        authority.configuration_sha256, "b" * 64, "/proof-state/draft.txt",
        "/proof-state/final.txt", "Draft", "Final: ", "\n",
    )
    plan_file.write_text(json.dumps(plan.to_payload()))
    plan_file.chmod(0o600)

    def build():
        owner = McpOwnerAuthority(timeline_engine, str(mandate_file))
        store = PostgresDurableGoalStore(
            timeline_engine, tenant_id=mandate["tenant_id"], installation_id=mandate["installation_id"]
        )
        store.admit(goal)
        guard = DocumentGoalAuthority(
            store=store, authority=owner, tool_configuration_sha256="b" * 64,
            read_plan=lambda: read_runtime_secret_file(str(plan_file), setting_name="document test plan"),
        )
        return store, guard

    store, guard = build()
    assert guard.authorize_effect(goal) == plan
    recreated_store, recreated = build()
    assert recreated_store.document_plan_binding(goal) == (plan.sha256, authority.configuration_sha256)
    assert recreated.authorize_effect(goal) == plan
    mandate_file.write_text(json.dumps({**mandate, "enabled": False}))
    with pytest.raises(ConversationConflict, match="stopped"):
        recreated.authorize_effect(goal)
    assert recreated.authorize_readback(goal) == plan
    assert len([e for e in store._events(goal) if e.event_type == "GOAL_DOCUMENT_PLAN_BOUND"]) == 1
    plan_file.write_text(json.dumps(replace(plan, first_content="Changed").to_payload()))
    with pytest.raises(DurableGoalError, match="changed"):
        recreated.authorize_effect(goal)
    # This fixture invokes no provider, model or conversation ingress.
