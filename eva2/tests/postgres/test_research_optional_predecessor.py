"""Strict model schemas allow first research without inventing a predecessor."""
import asyncio
from copy import deepcopy
from uuid import uuid4
import pytest
from jsonschema import ValidationError as JsonSchemaValidationError, validate
from pydantic import ValidationError as ContractValidationError
from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer
from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
from eva.application.conversation import ConversationActionCall, ConversationConflict
from eva.bootstrap.household_work import HouseholdWorkActions
from eva.contracts.delegated_work import PublicResearchRequest
from tests.postgres.test_household_public_work import setup, start, PUBLIC
from tests.postgres.test_scheduled_failure_return import database


def test_strict_research_schema_accepts_null_or_existing_predecessor():
    descriptor = next(t for t in PostgresDelegatedWorkActions.conversation_tools(include_research=True)
                      if t.name == "start_background_research")
    schema = OpenAIJsonSchemaTransformer(deepcopy(descriptor.input_schema), strict=True).walk()
    assert "prior_work_id" in schema["required"]
    for predecessor in (None, "work:previous"):
        validate({"research": PUBLIC, "prior_work_id": predecessor,
                  "acceptance_message": "Ik kijk ernaar."}, schema)



@pytest.mark.parametrize("mode", ["topic", "comparison"])
@pytest.mark.parametrize("empty_field", ["question", "title", "option"])
def test_research_schema_rejects_empty_strings_before_provider_transformation(mode, empty_field):
    descriptor = next(t for t in PostgresDelegatedWorkActions.conversation_tools(include_research=True)
                      if t.name == "start_background_research")
    research = {**PUBLIC, "research_mode": mode, "options": ["First topic", "Second topic"]}
    if empty_field == "option":
        research["options"][0] = ""
    else:
        research[empty_field] = ""
    with pytest.raises(ContractValidationError):
        PublicResearchRequest.model_validate(research)
    arguments = {"research": research, "prior_work_id": None, "acceptance_message": "I will investigate."}
    with pytest.raises(JsonSchemaValidationError):
        validate(arguments, descriptor.input_schema)
    # The installed provider transformer drops length bounds. Application
    # validation remains necessary; descriptor alignment cannot guarantee input.
    provider_schema = OpenAIJsonSchemaTransformer(deepcopy(descriptor.input_schema), strict=True).walk()
    properties = provider_schema["properties"]["research"]["properties"]
    field_schema = properties["options"]["items"] if empty_field == "option" else properties[empty_field]
    assert "minLength" not in field_schema
    validate(arguments, provider_schema)


@pytest.mark.parametrize("mode", ["topic", "comparison"])
def test_research_schema_preserves_valid_topic_and_comparison_requests(mode):
    descriptor = next(t for t in PostgresDelegatedWorkActions.conversation_tools(include_research=True)
                      if t.name == "start_background_research")
    research = {**PUBLIC, "research_mode": mode,
                "options": ["First topic"] if mode == "topic" else ["First topic", "Second topic"]}
    assert PublicResearchRequest.model_validate(research).to_payload() == research
    arguments = {"research": research, "prior_work_id": None, "acceptance_message": "I will investigate."}
    for schema in (descriptor.input_schema,
                   OpenAIJsonSchemaTransformer(deepcopy(descriptor.input_schema), strict=True).walk()):
        validate(arguments, schema)


@pytest.mark.parametrize("predecessor_mode", ["null", "absent", "existing"])
def test_research_predecessor_preserves_original_durable_call(database, predecessor_mode):
    x = setup(database, "research-optional-" + predecessor_mode)
    arguments = {"research": PUBLIC}
    prior = None
    if predecessor_mode == "existing":
        prior = start(x)
        assert asyncio.run(x.worker.run_once()) == "succeeded"
        arguments["prior_work_id"] = prior
    elif predecessor_mode == "null":
        arguments["prior_work_id"] = None
    actions = HouseholdWorkActions(database, x.service)
    turn = x.service.turn(str(uuid4()), PUBLIC["question"])
    call = ConversationActionCall("research:optional", "start_background_research", arguments)
    before = deepcopy(arguments)
    receipt = actions.execute(turn, call)
    assert actions.pending(turn) == receipt
    assert actions.execute(turn, call) == receipt
    assert call.arguments == before
    assert receipt.call.arguments == before
    work_id = receipt.output["operation_result"]["work"]["work_id"]
    saved = x.service._load(work_id, x.service.origin(turn))
    if prior is not None:
        assert saved.spec.parent_task_id == prior
    else:
        assert saved.spec.parent_task_id.startswith("household:")
    changed = {**arguments, "prior_work_id": "work:different"}
    with pytest.raises(ConversationConflict, match="changed after admission"):
        actions.execute(turn, ConversationActionCall(call.call_id, call.name, changed))


@pytest.mark.parametrize("prior", ["", 12, "x" * 241])
def test_invalid_explicit_predecessor_still_rejected(prior):
    service = type("Service", (), {"research_profile": object()})()
    actions = PostgresDelegatedWorkActions(None, service)
    with pytest.raises(ConversationConflict, match="predecessor"):
        actions.execute(None, ConversationActionCall("invalid", "start_background_research",
            {"research": PUBLIC, "prior_work_id": prior}))
