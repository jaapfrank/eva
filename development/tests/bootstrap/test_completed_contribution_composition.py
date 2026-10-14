from types import SimpleNamespace

from eva.bootstrap.completed_contributions import NAME, with_completed_contributions
from eva.bootstrap.delegated_digital_composition import build_digital_profile
from tests.bootstrap.test_delegated_digital_composition import configuration


def test_saved_result_handoff_is_in_both_catalog_and_new_grant_without_changing_old_grant():
    settings, bundle = configuration()
    old = build_digital_profile(settings, bundle).grant().to_payload()
    route = bundle.external_tools[0]
    route.private_owner_resolver = SimpleNamespace(_owner="owner", _binding="binding", _scope="scope")
    route.resolve_contribution_artifact = lambda *args: None
    composed = with_completed_contributions(settings, object(), bundle)
    assert [tool.name for tool in composed.conversation_tools].count(NAME) == 1
    assert sum(NAME in tool.action_names for tool in composed.external_tools) == 1
    assert with_completed_contributions(settings, object(), composed) is composed
    current = build_digital_profile(settings, composed).grant().to_payload()
    assert NAME in current["capability_ids"] and NAME not in old["capability_ids"]
    assert build_digital_profile(settings, bundle).grant().to_payload() == old
    assert current["maximum_model_runs"] == old["maximum_model_runs"]
    assert current["maximum_input_tokens"] == old["maximum_input_tokens"]


def test_plain_installation_does_not_get_unconfigured_handoff():
    settings, bundle = configuration()
    assert with_completed_contributions(settings, object(), bundle) is bundle
