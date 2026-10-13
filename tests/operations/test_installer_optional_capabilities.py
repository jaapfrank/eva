from copy import deepcopy
import pytest

from eva.contracts.canonical_json import canonical_sha256
from tools.installer_optional_capabilities import project_optional_capabilities


def plan(telegram="skipped", home="skipped"):
    value = {"contract": "EvaInstallerInstallationPlan/v2", "status": "ready_for_release_preparation",
             "blockers": [], "capabilities": [{"step_id": "telegram", "state": telegram},
                                                {"step_id": "home_assistant", "state": home}]}
    return {**value, "plan_sha256": canonical_sha256(value)}


def graph():
    return {"services": {
        "core": {"environment": {"EVA_HOME_ASSISTANT_URL": "configured", "EVA_OTHER": "keep"},
                 "volumes": [{"type": "bind", "target": "/run/secrets/home_assistant_token"}]},
        "telegram-ingress": {"depends_on": {"core": {}},
                             "volumes": [{"type": "bind", "target": "/run/secrets/telegram_bot_token"}]}}}


def test_selected_capabilities_preserve_existing_graph():
    original = graph(); value = plan("ready", "ready")
    result, omitted = project_optional_capabilities(original, value, value["plan_sha256"])
    assert result == original and not omitted


def test_skipped_capabilities_remove_only_their_consumers():
    original = graph(); before = deepcopy(original); value = plan()
    result, omitted = project_optional_capabilities(original, value, value["plan_sha256"])
    assert result["services"] == {"core": {"environment": {"EVA_OTHER": "keep"}, "volumes": []}}
    assert ("core", "/run/secrets/home_assistant_token") in omitted
    assert original == before


def test_changed_selection_cannot_reuse_confirmation():
    value = plan(); value["capabilities"][0]["state"] = "ready"
    with pytest.raises(ValueError, match="confirmed"):
        project_optional_capabilities(graph(), value, value["plan_sha256"])


def test_kept_service_dependency_is_not_silently_discarded():
    value = plan(); candidate = graph(); candidate["services"]["core"]["depends_on"] = {"telegram-ingress": {}}
    with pytest.raises(ValueError, match="depends on"):
        project_optional_capabilities(candidate, value, value["plan_sha256"])
