from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
from io import BytesIO
import json
import os
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import pytest

import eva.adapters.model.pydantic_conversation as conversation_module
from eva.adapters.objects.identity_verifier import load_current_identity_pack
from eva.adapters.connectors.home_assistant import HomeAssistantToolAdapter
from eva.adapters.connectors.home_assistant_automations import (
    HomeAssistantConversationAutomationPlanner,
)
from eva.adapters.connectors.home_assistant_management import (
    HomeAssistantConversationHacsPlanner,
    HomeAssistantConversationMigrationPlanner,
)
from eva.adapters.model.contracts import ConversationModelError
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.application.conversation import (
    ConversationActionCall,
    ConversationActionReceipt,
    ConversationModelTiming,
    ConversationModelUsage,
    ConversationObservationBinding,
    ConversationToolDescriptor,
    ConversationTurn,
    IdentityProfile,
    RecentConversationMessage,
)
from eva.application.memory import PresenceMemory
from eva.application.sensory import SensoryEvent
from eva.application.personal_interests import (
    CompletedPersonalExploration,
    CompletedPersonalPrototype,
    PersonalInterestClaim,
    PersonalInterestExploration,
    PersonalProductReleaseClaim,
    PersonalPrototypeClaim,
    PersonalPrototype,
)
from eva.application.research import ResearchSource
from eva.application.repository_improvements import (
    RepositoryCheck,
    RepositoryFile,
    RepositorySnapshot,
)
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.contracts.memory import MemoryRecall, RecalledFact
from eva.contracts.planning import ConfirmedFact
from eva.contracts.runtime_budget import RuntimeResourceBudget
from tests.support.scripted_pydantic_conversation import (
    ScriptedPhase, ScriptedPydanticConversation,
)


NOW = "2026-08-23T14:00:00Z"


@pytest.fixture(autouse=True)
def local_model_lease_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit leases local while production uses the fixed container contract."""

    monkeypatch.setattr(conversation_module, "_MODEL_LEASE_OWNER_UID", os.geteuid())
    monkeypatch.setattr(conversation_module, "_MODEL_LEASE_READER_GID", os.getegid())
    monkeypatch.setattr(
        conversation_module,
        "read_shared_runtime_file_bytes",
        lambda path, **_kwargs: Path(path).read_bytes(),
    )


def _deferred_tool(body: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [tool for tool in body["tools"] if tool.get("name") == name]
    assert len(matches) == 1
    return matches[0]


def _script_pydantic_phases(
    monkeypatch: pytest.MonkeyPatch,
    model: PydanticConversationModel,
    *responses: tuple[str, ConversationActionCall | None],
) -> list[dict[str, Any]]:
    """Stub the one public-framework phase without reviving a transport body."""

    remaining = iter(responses)
    phases: list[dict[str, Any]] = []

    def run(
        **phase: Any,
    ) -> tuple[
        str,
        ConversationActionCall | None,
        ConversationModelUsage,
        ConversationModelTiming,
    ]:
        phases.append(phase)
        text, action_call = next(remaining)
        return (
            text,
            action_call,
            ConversationModelUsage.measured(
                tool_calls=int(action_call is not None),
                input_tokens=11,
                output_tokens=max(1, len(text.split())),
            ),
            ConversationModelTiming(1 if text else None, 2),
        )

    monkeypatch.setattr(model, "_run_pydantic_phase", run)
    return phases


def _home_tool_model(**kwargs: Any) -> PydanticConversationModel:
    return PydanticConversationModel(
        conversation_tools=HomeAssistantToolAdapter.conversation_tools(),
        **kwargs,
    )


def test_action_tool_catalog_remains_canonically_byte_equal() -> None:
    assert canonical_sha256(conversation_module._ACTION_TOOLS) == (
        "e96adcb879caaf9a7ef864369c15d30db1b106603e8c37056b1e55c8511b6ca0"
    )
    assert not {
        "create_home_assistant_advanced_state_automation",
        "create_home_assistant_state_automation",
        "set_home_assistant_light",
        "set_home_assistant_switch",
        "read_home_assistant_history",
        "read_home_assistant_state",
        "plan_tuya_to_localtuya_migration",
        "register_approved_localtuya_repository",
        "install_approved_localtuya_package",
    } & set(conversation_module._ACTION_TOOLS)
    provider_tools = [
        {
            "completion_guidance": descriptor.completion_guidance,
            "description": descriptor.description,
            "input_schema": descriptor.input_schema,
            "name": descriptor.name,
        }
        for descriptor in HomeAssistantToolAdapter.conversation_tools()
    ]
    assert canonical_sha256(provider_tools) == (
        "2a14106fcc0b4a0a333f76f157a2dc129b37b763c1a7c9b259f888c2d03ae9b2"
    )
    automation_tools = [
        {
            "completion_guidance": descriptor.completion_guidance,
            "description": descriptor.description,
            "input_schema": descriptor.input_schema,
            "name": descriptor.name,
        }
        for descriptor in HomeAssistantConversationAutomationPlanner.conversation_tools()
    ]
    assert canonical_sha256(automation_tools) == (
        "1750683bef20a235a99e340d86b08ce814d02138b30b90f7328332b4ee733adc"
    )
    management_tools = [
        {
            "completion_guidance": descriptor.completion_guidance,
            "description": descriptor.description,
            "input_schema": descriptor.input_schema,
            "name": descriptor.name,
        }
        for descriptor in HomeAssistantConversationMigrationPlanner.conversation_tools()
    ]
    assert canonical_sha256(management_tools) == (
        "52cb6f8c8c041f02b88f5f1b9b4f9a9e32903ca3f819b3db49a0d80d6031c04e"
    )
    hacs_tools = [
        {
            "completion_guidance": descriptor.completion_guidance,
            "description": descriptor.description,
            "input_schema": descriptor.input_schema,
            "name": descriptor.name,
        }
        for descriptor in HomeAssistantConversationHacsPlanner.conversation_tools()
    ]
    assert canonical_sha256(hacs_tools) == (
        "9e9740cb5b32560fdf313334c2672783b07b8ef5f43c3d9c4365f53fd57a289d"
    )
    assert {
        name: canonical_sha256(getattr(conversation_module, name))
        for name in (
            "_MEMORY_SEARCH_TOOL",
            "_REPOSITORY_PROPOSAL_TOOL",
            "_PROTOTYPE_TOOL",
            "_LISTING_TOOL",
            "_FREE_WILL_TOOL",
        )
    } == {
        "_MEMORY_SEARCH_TOOL": "3b283cb89ebf253f0650dd55739d24410566b6d1e34aaf76fbe9edd302586727",
        "_REPOSITORY_PROPOSAL_TOOL": "d19c74dcf7f7a81747448558dbf1f573d28ff8044c6250bd62ccba6a9ab2a34c",
        "_PROTOTYPE_TOOL": "fc225d60d8fa19d2a3542ff528cf5554f90f75d5404d137c642aa00df7a698af",
        "_LISTING_TOOL": "401df39c0d1c0bc41fc1a35c3c008cfbcd4ece9bf9c3b7f1e406b9e3b25de7cb",
        "_FREE_WILL_TOOL": "2959078dc6323da786622599f7631ec74de2f38693d9cb144475609030788895",
    }


def test_external_provider_catalog_needs_no_model_specific_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    descriptor = ConversationToolDescriptor(
        name="lookup_calendar_slot",
        description="Lees één beschikbaar tijdvak uit de gekoppelde agenda.",
        input_schema={
            "additionalProperties": False,
            "properties": {"day": {"type": "string"}},
            "required": ["day"],
            "type": "object",
        },
        completion_guidance=(
            "Meld alleen het tijdvak dat uit het gecontroleerde toolresultaat blijkt."
        ),
    )
    model = PydanticConversationModel(
        auth_file=tmp_path / "unused-auth.json",
        conversation_tools=(descriptor,),
    )
    phases = _script_pydantic_phases(
        monkeypatch,
        model,
        (
            "",
            ConversationActionCall(
                "calendar-call-1", "lookup_calendar_slot", {"day": "morgen"}
            ),
        ),
        ("Morgen is 10:00 vrij.", None),
    )
    turn = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message-calendar",
        "Wanneer kan ik morgen?",
        NOW,
    )
    selected = model.generate(
        turn,
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        _memory(),
        (),
        available_actions=("lookup_calendar_slot",),
    )
    assert selected.action_call is not None
    completed = model.generate(
        turn,
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        _memory(),
        (),
        completed_action=ConversationActionReceipt(
            selected.action_call, {"available": True, "start": "10:00"}
        ),
    )

    assert completed.text == "Morgen is 10:00 vrij."
    assert _deferred_tool(phases[0], "lookup_calendar_slot")["parameters"] == (
        descriptor.input_schema
    )
    assert phases[0]["completed_action"] is None
    assert phases[1]["completed_action"].call == selected.action_call
    assert phases[1]["tools"] == ()
    assert descriptor.completion_guidance in phases[1]["instructions"]
    assert "lookup_calendar_slot" not in conversation_module._ACTION_TOOLS


def test_bound_photo_correction_uses_closed_action_without_face_inference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json")
    call = ConversationActionCall(
        "identity-correction-call-1",
        "record_observation_identity_correction",
        {"corrected_name": "Mark"},
    )
    phases = _script_pydantic_phases(monkeypatch, model, ("", call))
    binding = ConversationObservationBinding(
        "telegram-photo:mark-1",
        "a" * 64,
        "b" * 64,
        "image/jpeg",
        "telegram:file:mark-1",
    )
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-photo-correction",
            "Nee, dat is Mark.",
            NOW,
            observation_binding=binding,
        ),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        _memory(),
        (),
        available_actions=("record_observation_identity_correction",),
    )

    assert result.action_call == call
    assert _deferred_tool(phases[0], "record_observation_identity_correction")
    assert binding.binding_sha256 in phases[0]["prompt"]
    assert (
        "Leid zelf geen gezicht of naam"
        in _deferred_tool(phases[0], "record_observation_identity_correction")[
            "description"
        ]
    )


def test_validated_file_edit_is_a_closed_model_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json")
    call = ConversationActionCall(
        "validated-edit-call-1",
        "edit_text_file_with_focused_validation",
        {
            "new_text": "MODE=turbo",
            "old_text": "MODE=safe",
            "target_path": "project/settings.env",
            "test_manifest_path": "project/focused-test.json",
        },
    )
    phases = _script_pydantic_phases(monkeypatch, model, ("", call))
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-edit",
            (
                "Vervang MODE=safe door MODE=turbo in project/settings.env en "
                "draai project/focused-test.json."
            ),
            NOW,
        ),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        _memory(),
        (),
        available_actions=("edit_text_file_with_focused_validation",),
    )

    assert result.action_call == call
    assert _deferred_tool(phases[0], "edit_text_file_with_focused_validation")
    assert (
        "uitsluitend deze wijziging terug"
        in _deferred_tool(phases[0], "edit_text_file_with_focused_validation")[
            "description"
        ]
    )


@pytest.mark.parametrize("prompt,instructions", [("", "valid"), ("valid", "")])
def test_tool_free_text_generation_rejects_empty_inputs(
    tmp_path: Path, prompt: str, instructions: str
) -> None:
    model = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json")
    with pytest.raises(ValueError, match="tool-free conversation input is invalid"):
        model.generate_tool_free_text(prompt=prompt, instructions=instructions)


def test_repository_improvement_is_self_chosen_from_a_bounded_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json")
    snapshot = RepositorySnapshot(
        project_directory="fixture-repo",
        repository_sha256="a" * 64,
        checks_manifest_sha256="b" * 64,
        files=(RepositoryFile("settings.toml", "retry_limit = 0\n", "c" * 64),),
        checks=(
            RepositoryCheck(
                "bounded_retry",
                "settings.toml",
                "contains",
                "retry_limit = 2",
                False,
                "d" * 64,
            ),
        ),
        captured_at=datetime(2026, 8, 24, 8, 0, tzinfo=UTC),
    )
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        bodies.append(body)
        return "", ConversationActionCall(
            "proposal-1",
            "submit_repository_improvement",
            {
                "check_id": "bounded_retry",
                "evidence": "settings.toml bevat retry_limit = 0.",
                "new_text": "retry_limit = 2",
                "old_text": "retry_limit = 0",
                "problem": "Een tijdelijke fout krijgt geen begrensde retry.",
                "target_path": "settings.toml",
            },
        )

    monkeypatch.setattr(model, "_request", request)
    proposal = model.choose_repository_improvement(snapshot)

    assert proposal.check_id == "bounded_retry"
    assert proposal.target_path == "settings.toml"
    assert bodies[0]["tool_choice"]["name"] == "submit_repository_improvement"
    assert "retry_limit = 0" in bodies[0]["input"][0]["content"]
    assert "Kies zelf precies één werkelijk falende check" in bodies[0]["instructions"]


def _write_access_lease(path: Path, access_token: str = "access") -> None:
    path.write_text(
        json.dumps(
            {
                "access_token": access_token,
                "account_id": "account",
                "contract": "EvaModelAccessLease/v1",
                "source_sha256": "0" * 64,
            }
        )
    )
    path.chmod(0o640)


def _runtime_budget(
    *, max_input_tokens: int = 12_000, max_output_tokens: int = 1_200
) -> RuntimeResourceBudget:
    values = {
        "background_reflections_per_installation_day": 40,
        "background_reflections_per_person_day": 4,
        "contract": "EvaInstallerBudget/v2",
        "external_monthly_spend_cap_eur_cents": 0,
        "max_input_tokens": max_input_tokens,
        "max_output_tokens": max_output_tokens,
        "memory_bytes_per_person": 20_971_520,
        "memory_bytes_per_group": 20_971_520,
        "memory_bytes_per_installation": 209_715_200,
        "memory_bytes_per_project": 20_971_520,
        "memory_records_per_person": 2_000,
        "memory_records_per_group": 2_000,
        "memory_records_per_installation": 20_000,
        "memory_records_per_project": 2_000,
        "profile_context_tokens": 800,
        "recall_context_tokens": 3_000,
        "retention_days": 365,
        "retrieval_nodes": 50,
        "unknown_cost_policy": "block_autonomous_spend",
    }
    return RuntimeResourceBudget(
        tenant_id="tenant:test",
        installation_id="installation:test",
        source_budget_sha256=hashlib.sha256(canonical_json_bytes(values)).hexdigest(),
        recall_context_tokens=3_000,
        retrieval_nodes=50,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        profile_context_tokens=800,
        memory_records_per_person=2_000,
        memory_bytes_per_person=20_971_520,
        memory_records_per_group=2_000,
        memory_bytes_per_group=20_971_520,
        memory_records_per_project=2_000,
        memory_bytes_per_project=20_971_520,
        memory_records_per_installation=20_000,
        memory_bytes_per_installation=209_715_200,
        retention_days=365,
        background_reflections_per_person_day=4,
        background_reflections_per_installation_day=40,
        external_monthly_spend_cap_eur_cents=0,
    )


@contextmanager
def _budget_provider(
    frames: bytes,
) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            size = int(self.headers["Content-Length"])
            requests.append(json.loads(self.rfile.read(size)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(frames)))
            self.end_headers()
            self.wfile.write(frames)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/responses", requests
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _budget_frames(
    *,
    input_tokens: int = 100,
    output_tokens: int = 20,
    status: str = "completed",
    error: object = None,
    include_usage: bool = True,
    trailing_data: bool = False,
) -> bytes:
    usage = (
        {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
        if include_usage
        else None
    )
    completed = json.dumps(
        {
            "response": {"error": error, "status": status, "usage": usage},
            "type": "response.completed",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    suffix = (
        'data: {"type":"response.output_text.delta","delta":"te laat"}\n\n'
        if trailing_data
        else "data: [DONE]\n\n"
    )
    return (
        "event: response.output_text.delta\n"
        'data: {"type":"response.output_text.delta","delta":"Binnen grens."}\n\n'
        "event: response.completed\n"
        f"data: {completed}\n\n"
        f"{suffix}"
    ).encode()


def test_runtime_budget_rejects_oversized_input_before_auth_or_network(
    tmp_path: Path,
) -> None:
    with _budget_provider(_budget_frames()) as (endpoint, requests):
        model = PydanticConversationModel(
            auth_file=tmp_path / "missing-auth.json",
            endpoint=endpoint,
            resource_budget=_runtime_budget(max_input_tokens=4_000),
        )

        with pytest.raises(ConversationModelError, match="input budget"):
            model._request(  # noqa: SLF001 - this is the provider admission boundary
                {"input": [{"content": " woord" * 4_000, "role": "user"}]}
            )

    assert requests == []


@pytest.mark.parametrize("public_framework", [False, True])
@pytest.mark.parametrize("unanswered_retries", [0, 3])
def test_long_conversation_selects_recent_complete_context_before_model_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, public_framework: bool,
    unanswered_retries: int,
) -> None:
    scripted = ScriptedPydanticConversation((ScriptedPhase.text("Ja, dat doen we."),))
    model = PydanticConversationModel(
        auth_file=tmp_path / "unused-auth.json", resource_budget=_runtime_budget(),
        model_backend=scripted.model if public_framework else None,
    )
    phases = ([] if public_framework else
              _script_pydantic_phases(monkeypatch, model, ("Ja, dat doen we.", None)))
    turn = ConversationTurn.private(
        "tenant", "installation", "jaap", "conversation", "message",
        "Ja, doe dat maar.", NOW,
    )
    identity = IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80)
    history = tuple(
        RecentConversationMessage(role, f"oude beurt {n} " + "verhaal " * 2000, NOW)
        for n in range(5) for role in ("user", "assistant")
    ) + (
        RecentConversationMessage("user", "Kun je een plan voor morgen maken?", NOW),
        RecentConversationMessage("assistant", "Zal ik beginnen met de boodschappen?", NOW),
    ) + tuple(RecentConversationMessage("user", f"Nog geen antwoord {n}", NOW)
              for n in range(unanswered_retries))
    original = tuple(history)

    result = model.generate(turn, identity, _memory(), history)

    assert result.text == "Ja, dat doen we."
    if public_framework:
        assert len(scripted.captures) == 1
        assert scripted.remaining_phases == 0
        phases.append({"prompt": turn.text,
                       "instructions": scripted.captures[0].instructions,
                       "tools": (conversation_module._MEMORY_SEARCH_TOOL,)})
    assert len(phases) == 1
    phase = phases[0]
    model._admit_request({  # noqa: SLF001
        "input": [{"content": phase["prompt"], "role": "user", "type": "message"}],
        "instructions": phase["instructions"], "store": False,
        "tools": [{"execution": "server", "type": "tool_search"}]
        + [{**tool, "defer_loading": True} for tool in phase["tools"]],
    }, autonomous=False)
    assert "oude beurt 0" not in phase["instructions"]
    assert "Kun je een plan voor morgen maken?" in phase["instructions"]
    assert "Zal ik beginnen met de boodschappen?" in phase["instructions"]
    for n in range(unanswered_retries):
        assert f"Nog geen antwoord {n}" in phase["instructions"]
    assert "Jaap houdt niet van formele antwoorden" in phase["instructions"]
    assert "Wees eerlijk." in phase["instructions"]
    assert "Oudere gespreksbeurten zijn buiten deze invoer gelaten" in phase["instructions"]
    assert phase["prompt"] == turn.text
    assert history == original


@pytest.mark.parametrize("oversized_section", ["current_question", "latest_exchange", "receipt"])
def test_context_selection_never_discards_required_content_to_admit_request(
    tmp_path: Path, oversized_section: str,
) -> None:
    scripted = ScriptedPydanticConversation((ScriptedPhase.text("must not run"),))
    model = PydanticConversationModel(
        auth_file=tmp_path / "unused-auth.json", model_backend=scripted.model,
        resource_budget=_runtime_budget(),
    )
    long_text = " x" * 12000
    turn = ConversationTurn.private(
        "tenant", "installation", "jaap", "conversation", "message",
        long_text if oversized_section == "current_question" else "Ja, doe dat.", NOW,
    )
    latest = long_text if oversized_section == "latest_exchange" else "Het voorstel."
    history = tuple(RecentConversationMessage("user", f"oud {i}", NOW) for i in range(10)) + (
        RecentConversationMessage("user", "Mijn laatste vraag.", NOW),
        RecentConversationMessage("assistant", latest, NOW),
    )
    receipt = (ConversationActionReceipt(
        ConversationActionCall("test-call", "synthetic_receipt", {}),
        {"result": long_text},
    ) if oversized_section == "receipt" else None)
    with pytest.raises(ConversationModelError, match="input budget"):
        model.generate(
            turn, IdentityProfile("Eva", "Huisgenoot.", ("Wees eerlijk.",), 80),
            _memory(), history, completed_action=receipt,
        )
    assert scripted.captures == []


def test_runtime_budget_admits_only_the_exact_deferred_catalog(tmp_path: Path) -> None:
    budget = _runtime_budget()
    from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
    from eva.providers.mcp_discovery import McpDiscoveryToolAdapter
    from eva.providers.mcp_registration import registration_descriptors

    provider_tools = (
        *HomeAssistantToolAdapter.conversation_tools(),
        *HomeAssistantConversationAutomationPlanner.conversation_tools(),
        *HomeAssistantConversationMigrationPlanner.conversation_tools(),
        *HomeAssistantConversationHacsPlanner.conversation_tools(),
        *McpDiscoveryToolAdapter.conversation_tools(),
        *registration_descriptors(),
        *PostgresDelegatedWorkActions.conversation_tools(),
    )
    model = PydanticConversationModel(
        auth_file=tmp_path / "missing-auth.json",
        resource_budget=budget,
        conversation_tools=provider_tools,
    )
    catalog = [
        {"execution": "server", "type": "tool_search"},
        {
            **conversation_module._MEMORY_SEARCH_TOOL,
            "defer_loading": True,
        },
        *[
            {**tool, "defer_loading": True}
            for tool in {
                **conversation_module._ACTION_TOOLS,
                **model._conversation_tools,  # noqa: SLF001
            }.values()
        ],
    ]
    body = {"input": [{"content": "hoi", "role": "user"}], "tools": catalog}
    assert len(canonical_json_bytes(catalog)) > 20_000
    assert len(canonical_json_bytes(body)) + 256 > budget.max_input_tokens

    model._admit_request(body, autonomous=False)  # noqa: SLF001

    tampered = json.loads(json.dumps(body))
    tampered["tools"][1]["description"] += " gewijzigd"
    with pytest.raises(ConversationModelError, match="deferred tool catalog"):
        model._admit_request(tampered, autonomous=False)  # noqa: SLF001

    duplicated = json.loads(json.dumps(body))
    duplicated["tools"].append(duplicated["tools"][1])
    unknown = json.loads(json.dumps(body))
    unknown["tools"][1]["name"] = "unregistered_owner_tool"
    oversized = json.loads(json.dumps(body))
    oversized["tools"][1]["description"] += "x" * 100_000
    for rejected in (duplicated, unknown, oversized):
        with pytest.raises(ConversationModelError, match="deferred tool catalog"):
            model._admit_request(rejected, autonomous=False)  # noqa: SLF001

    over_budget = {**body, "input": [{"content": "woord " * 50_000, "role": "user"}]}
    with pytest.raises(ConversationModelError, match="input budget"):
        model._admit_request(over_budget, autonomous=False)  # noqa: SLF001


def test_runtime_budget_blocks_unknown_autonomous_spend_before_network(
    tmp_path: Path,
) -> None:
    with _budget_provider(_budget_frames()) as (endpoint, requests):
        model = PydanticConversationModel(
            auth_file=tmp_path / "missing-auth.json",
            endpoint=endpoint,
            resource_budget=_runtime_budget(),
        )

        assert model.autonomous_admission_blocker() == "unverified_model_cost"
        with pytest.raises(ConversationModelError, match="no verified price"):
            model._request_autonomous(  # noqa: SLF001
                {"input": [{"content": "kleine taak", "role": "user"}]},
            )

    assert requests == []


def test_existing_subscription_allows_autonomy_without_extra_daily_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from io import BytesIO

    auth = tmp_path / "subscription-lease.json"
    _write_access_lease(auth)
    model = PydanticConversationModel(auth_file=auth, resource_budget=_runtime_budget())
    calls = []

    def respond(request, **kwargs):
        assert request.full_url == "https://chatgpt.com/backend-api/codex/responses"
        assert request.get_header("Authorization") == "Bearer access"
        calls.append(request)
        return BytesIO(_budget_frames())

    monkeypatch.setattr(conversation_module.urllib.request, "urlopen", respond)
    assert model.autonomous_admission_blocker() is None
    # More than the unrelated reflection quota: no invented daily model-call cap.
    for _ in range(41):
        assert model._request_autonomous({"input": [{"content": "kleine taak", "role": "user"}]}) == (
            "Binnen grens.", None,
        )
    assert len(calls) == 41  # exactly one dispatch per request, no extra retry


@pytest.mark.parametrize("endpoint", [
    "https://api.openai.com/v1/responses",
    "https://chatgpt.com.example.invalid/backend-api/codex/responses",
    "https://chatgpt.com/backend-api/codex/responses?billing=api",
    "http://chatgpt.com/backend-api/codex/responses",
])
def test_subscription_admission_does_not_authorize_other_endpoints(tmp_path: Path, endpoint: str) -> None:
    model = PydanticConversationModel(
        auth_file=tmp_path / "absent.json", endpoint=endpoint, resource_budget=_runtime_budget(),
    )
    assert model.autonomous_admission_blocker() == "unverified_model_cost"
    with pytest.raises(ConversationModelError, match="no verified price"):
        model._request_autonomous({"input": [{"content": "taak", "role": "user"}]})


@pytest.mark.parametrize("frames", [
    _budget_frames(include_usage=False), _budget_frames(output_tokens=1201),
])
def test_subscription_autonomy_still_requires_valid_bounded_usage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, frames: bytes,
) -> None:
    from io import BytesIO

    auth = tmp_path / "subscription-lease.json"
    _write_access_lease(auth)
    model = PydanticConversationModel(auth_file=auth, resource_budget=_runtime_budget())
    monkeypatch.setattr(conversation_module.urllib.request, "urlopen", lambda *a, **kw: BytesIO(frames))
    with pytest.raises(ConversationModelError):
        model._request_autonomous({"input": [{"content": "taak", "role": "user"}]})


def test_subscription_autonomy_does_not_retry_provider_limit_or_missing_auth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from io import BytesIO
    from urllib.error import HTTPError

    auth = tmp_path / "subscription-lease.json"
    model = PydanticConversationModel(auth_file=auth, resource_budget=_runtime_budget())
    calls = []

    def limited(request, **kwargs):
        calls.append(request)
        raise HTTPError(request.full_url, 429, "limit", {}, BytesIO(b"{}"))

    monkeypatch.setattr(conversation_module.urllib.request, "urlopen", limited)
    body = {"input": [{"content": "taak", "role": "user"}]}
    with pytest.raises(ConversationModelError, match="auth file is unavailable"):
        model._request_autonomous(body)
    assert calls == []
    _write_access_lease(auth)
    with pytest.raises(ConversationModelError, match="HTTP 429"):
        model._request_autonomous(body)
    assert len(calls) == 1


def _memory() -> PresenceMemory:
    fact = ConfirmedFact(
        fact_id="hindsight:memory-1",
        subject="jaap",
        predicate="memory.observation",
        value={"text": "Jaap houdt niet van formele antwoorden."},
        source_ref="telegram:history",
        evidence_ref="hindsight:memory-1",
        observed_at=NOW,
        trust="host_policy",
    )
    recall = MemoryRecall(
        schema_version=1,
        contract="MemoryRecall/v1",
        recall_id="recall:1",
        query_purpose="conversation_reply",
        tenant_id="tenant",
        principal_id="jaap",
        conversation_id="conversation",
        project_id=None,
        requested_valid_at=NOW,
        admitted_fact_ids_sha256=canonical_sha256([fact.fact_id]),
        selected=[
            RecalledFact(
                rank=1,
                fact_id=fact.fact_id,
                source_event_ids=[fact.source_ref],
                trust="model_inference_unconfirmed",
                ranking_reason="test",
            ).model_dump(mode="json")
        ],
        excluded_reasons={},
        projection_version="hindsight-0.9.1-v1",
        scope_sha256=canonical_sha256({"kind": "person"}),
        created_at=NOW,
    )
    return PresenceMemory(
        recall,
        (fact,),
        "Jaap wil korte, directe en warme antwoorden zonder onnodige kopjes.",
    )


def test_direct_model_receives_recalled_memories_without_reflection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json")
    phases = _script_pydantic_phases(monkeypatch, model, ("Dat klinkt echt rot.", None))
    turn = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message",
        "Ik heb een rotdag.",
        NOW,
    )
    identity = IdentityProfile(
        "Eva",
        "Je bent een warme digitale huisgenoot.",
        ("Wees eerlijk.",),
        80,
        load_current_identity_pack(),
    )

    result = model.generate(turn, identity, _memory(), ())

    assert result.text == "Dat klinkt echt rot."
    assert result.provider == "chatgpt-codex:gpt-5.4-mini"
    assert phases[0]["prompt"] == turn.text
    assert phases[0]["tools"] == (conversation_module._MEMORY_SEARCH_TOOL,)
    instructions = phases[0]["instructions"]
    for fragment in (
        "Jaap houdt niet van formele antwoorden",
        "waargenomen 2026-08-23T14:00:00Z",
        "nieuwste betrouwbare bron",
        "Duurzame stijlspiegel",
        "Actief procedureel IdentityPack",
        "Stem en temperament: warm, practical, curious, concise, slightly_stubborn",
        "temperament en humor als zichtbaar gedrag",
        "opvallende tegenstelling",
        "concreet detail dat al in de situatie zit",
        "kort, droog en in gewone spreektaal",
        "gemaakte woordspeling, verzonnen samenstelling, catchphrase",
        "duidelijkheid, warmte en bruikbaarheid gaan voor",
        "Forceer nooit humor bij verdriet, gevaar of angst",
        "niet stijver of langer om identiteit te tonen",
        "claim alleen wat aantoonbaar is gelukt",
        "Jaap wil korte, directe en warme antwoorden",
        "kopieer geen zinnen, typefouten of boosheid",
        "één expliciete relevante herinnering",
        "Verzin dan geen voorbeeldzin",
        "geen ongevraagd aanbod of vraag",
        "nooit een generiek dienstenmenu",
        "eigen digitaal perspectief",
        "leg het werk niet terug",
        "Vervang het doel niet door een makkelijker buurdoel",
        "Behoud alle expliciet gevraagde resultaten uit de volledige opdracht",
        "Voer de gevraagde eigen handelingen uit",
        "uitgesproken grens",
        "Verzin geen fysieke feiten",
        "kan geen toekomstig werk inplannen",
        "geen tijdstip of latere terugkoppeling",
        "antwoord als jezelf, niet vanuit een vaste antwoordvorm",
    ):
        assert fragment in instructions
    assert instructions.count("Actief procedureel IdentityPack") == 1
    assert instructions.count("slightly_stubborn") == 1
    assert "jong of vlot" not in instructions
    assert "/reflect" not in instructions


def test_direct_model_projects_configured_identity_without_fixed_eva_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json")
    phases = _script_pydantic_phases(
        monkeypatch, model, ("Ik blijf bij de feiten.", None)
    )
    pack = load_current_identity_pack()
    voice_style = pack.voice_style.model_copy(
        update={"qualities": ["calm", "playful", "concise"]}
    )
    configured_pack = pack.model_copy(
        update={
            "display_name": "Noor",
            "humour": "droog en spaarzaam",
            "temperament": ["analytical", "kind", "calm"],
            "voice_style": voice_style,
        }
    )

    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "configured-identity",
            "Wat vind jij hiervan?",
            NOW,
        ),
        IdentityProfile(
            "Noor",
            "Je bent een oplettende digitale huisgenoot.",
            ("Wees eerlijk.",),
            80,
            configured_pack,
        ),
        _memory(),
        (),
        available_actions=(conversation_module._PERSONAL_INTEREST_ACTION,),
    )

    assert result.text == "Ik blijf bij de feiten."
    instructions = phases[0]["instructions"]
    assert "Je bent Noor." in instructions
    assert "Stem en temperament: calm, playful, concise, analytical, kind" in instructions
    assert "Humor: droog en spaarzaam" in instructions
    assert "Eva" not in instructions
    interest_tool = _deferred_tool(phases[0], conversation_module._PERSONAL_INTEREST_ACTION)
    encoded_tool = json.dumps(interest_tool, ensure_ascii=False)
    assert "Eva" not in encoded_tool
    assert "door jou zelf gekozen" in encoded_tool
    assert "bij je past" in encoded_tool
    assert "je hierover wilt onderzoeken" in encoded_tool


def test_recent_durable_conversation_is_not_denied_as_missing_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json")
    phases = _script_pydantic_phases(monkeypatch, model, ("amberkleurige maan", None))
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-after-restart",
            "Welke twee woorden moest je onthouden?",
            NOW,
        ),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        _memory(),
        (
            RecentConversationMessage(
                "Jaap",
                "Onthoud voor deze herstartproef: amberkleurige maan.",
                "2026-08-23T13:59:00Z",
            ),
        ),
    )

    assert result.text == "amberkleurige maan"
    instructions = phases[0]["instructions"]
    assert "duurzame gespreksgeschiedenis" in instructions
    assert "een herstart van core of databasecontainer overleven" in instructions
    assert "niet kunt onthouden" in instructions
    assert "amberkleurige maan" in instructions
    assert [tool["name"] for tool in phases[0]["tools"]] == ["search_personal_memory"]
    assert _deferred_tool(phases[0], "search_personal_memory")


def test_document_work_action_is_model_chosen_but_episode_bound(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json")
    phases = _script_pydantic_phases(
        monkeypatch,
        model,
        (
            "",
            ConversationActionCall("document-call-1", "checkpoint_document_task", {}),
        ),
    )
    turn = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message",
        "Ga hiermee door.",
        NOW,
    )
    identity = IdentityProfile(
        "Eva",
        "Je bent een warme digitale huisgenoot.",
        ("Wees eerlijk.",),
        80,
        load_current_identity_pack(),
    )

    result = model.generate(
        turn,
        identity,
        _memory(),
        (),
        available_actions=("checkpoint_document_task",),
        self_context=("Direct actieve documenttaak in dit gesprek: checklist.md.",),
    )

    assert result.action_call is not None
    assert result.action_call.name == "checkpoint_document_task"
    assert _deferred_tool(phases[0], "checkpoint_document_task")
    instructions = phases[0]["instructions"]
    assert (
        "direct actieve taak uit dit gesprek"
        in _deferred_tool(phases[0], "checkpoint_document_task")["description"]
    )
    assert "Direct actieve documenttaak in dit gesprek" in instructions


def test_dynamic_selection_uses_one_pydantic_deferred_tool_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = _home_tool_model(
        auth_file=tmp_path / "unused-auth.json", reasoning_effort="high"
    )
    phases = _script_pydantic_phases(
        monkeypatch,
        model,
        (
            "",
            ConversationActionCall(
                "state-call-configuration",
                "read_home_assistant_state",
                {"entity_kind": "switch", "target": "verwarming"},
            ),
        ),
    )
    result = model.generate(
        ConversationTurn.private(
            "tenant", "installation", "jaap", "conversation", "message", "Hoi", NOW
        ),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        _memory(),
        (),
        available_actions=("read_home_assistant_state",),
    )
    assert result.action_call is not None
    assert result.action_call.name == "read_home_assistant_state"
    assert [tool["name"] for tool in phases[0]["tools"]] == [
        "search_personal_memory",
        "read_home_assistant_state",
    ]

    with pytest.raises(ValueError, match="reasoning effort"):
        PydanticConversationModel(
            auth_file=tmp_path / "unused-auth.json",
            reasoning_effort="extreme",  # type: ignore[arg-type]
        )


def test_dynamic_catalog_defers_memory_and_available_actions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = _home_tool_model(auth_file=tmp_path / "unused-auth.json")
    phases = _script_pydantic_phases(
        monkeypatch,
        model,
        (
            "",
            ConversationActionCall(
                "memory-query-1",
                "search_personal_memory",
                {"query_text": "overkapping veranda temperatuur warm koel voorkeur"},
            ),
        ),
    )
    turn = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message-memory-query",
        "Welke kant moet de temperatuur onder de overkapping volgens mij op?",
        NOW,
    )

    result = model.generate(
        turn,
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        _memory(),
        (),
        available_actions=("read_home_assistant_state", "set_home_assistant_light"),
    )

    assert result.action_call is not None
    assert result.action_call.name == "search_personal_memory"
    assert [tool["name"] for tool in phases[0]["tools"]] == [
        "search_personal_memory",
        "read_home_assistant_state",
        "set_home_assistant_light",
    ]
    assert set(phases[0]["tools"][0]["parameters"]["properties"]) == {"query_text"}


def test_full_dynamic_conversation_offers_exact_pydantic_deferred_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = _home_tool_model(auth_file=tmp_path / "unused-auth.json")
    phases = _script_pydantic_phases(monkeypatch, model, ("Binnen grens.", None))
    model.generate(
        ConversationTurn.private(
            "tenant", "installation", "jaap", "conversation", "message", "Hoi", NOW
        ),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        _memory(),
        (),
        available_actions=tuple(conversation_module._ACTION_TOOLS),
    )

    assert [tool["name"] for tool in phases[0]["tools"]] == [
        "search_personal_memory",
        *conversation_module._ACTION_TOOLS,
    ]


def test_model_reports_an_expired_access_lease_without_provider_call(
    tmp_path: Path,
) -> None:
    def encoded(value: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    token = f"{encoded({'alg': 'none'})}.{encoded({'exp': 1})}.signature"
    auth_file = tmp_path / "lease.json"
    auth_file.write_text(
        json.dumps(
            {
                "access_token": token,
                "account_id": "account",
                "contract": "EvaModelAccessLease/v1",
                "source_sha256": "0" * 64,
            }
        )
    )
    auth_file.chmod(0o640)

    model = PydanticConversationModel(auth_file=auth_file)

    status = model.credential_status(now=datetime(2026, 8, 23, tzinfo=UTC))
    assert status.ready is False
    assert status.state == "expired"


def test_model_rejects_a_full_refresh_authority_file(tmp_path: Path) -> None:
    auth_file = tmp_path / "full-auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "access",
                    "account_id": "account",
                    "refresh_token": "refresh-must-stay-with-owner",
                }
            }
        )
    )
    auth_file.chmod(0o640)

    status = PydanticConversationModel(auth_file=auth_file).credential_status()

    assert status.ready is False
    assert status.state == "invalid"


def test_provider_rejection_latches_until_the_access_lease_rotates(
    tmp_path: Path,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        auth_file = tmp_path / "lease.json"
        auth_file.write_text(
            json.dumps(
                {
                    "access_token": "rejected-access",
                    "account_id": "account",
                    "contract": "EvaModelAccessLease/v1",
                    "source_sha256": "1" * 64,
                }
            )
        )
        auth_file.chmod(0o640)
        host, port = server.server_address
        model = PydanticConversationModel(
            auth_file=auth_file, endpoint=f"http://{host}:{port}/responses"
        )

        with pytest.raises(ConversationModelError, match="credentials were rejected"):
            model._request({})
        assert model.credential_status().state == "rejected"

        replacement = auth_file.with_suffix(".new")
        replacement.write_text(
            json.dumps(
                {
                    "access_token": "rotated-access",
                    "account_id": "account",
                    "contract": "EvaModelAccessLease/v1",
                    "source_sha256": "2" * 64,
                }
            )
        )
        replacement.chmod(0o640)
        replacement.replace(auth_file)
        assert model.credential_status().state == "ready"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_stream_read_timeout_is_a_bounded_provider_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class TimedOutStream:
        def __iter__(self) -> "TimedOutStream":
            return self

        def __next__(self) -> bytes:
            raise TimeoutError("synthetic stream read timeout")

        def close(self) -> None:
            pass

    auth_file = tmp_path / "lease.json"
    _write_access_lease(auth_file)
    model = PydanticConversationModel(auth_file=auth_file)
    monkeypatch.setattr(
        conversation_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: TimedOutStream(),
    )

    with pytest.raises(ConversationModelError, match="provider is unavailable"):
        model._request({})


def test_model_can_choose_a_verified_switch_without_treating_it_as_a_light(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = _home_tool_model(auth_file=tmp_path / "unused-auth.json")
    phases = _script_pydantic_phases(
        monkeypatch,
        model,
        (
            "",
            ConversationActionCall(
                "switch-call-1",
                "set_home_assistant_switch",
                {"state": "on", "target": "verwarming in de overkapping"},
            ),
        ),
    )
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-switch",
            "Zet de verwarming in de overkapping aan.",
            NOW,
        ),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        _memory(),
        (
            RecentConversationMessage(
                "Jaap", "Het is warm in de overkapping.", "2026-08-23T13:59:00Z"
            ),
            RecentConversationMessage(
                "Eva",
                "Ik kan de verwarming in de overkapping uitzetten.",
                "2026-08-23T13:59:30Z",
            ),
        ),
        available_actions=("set_home_assistant_switch",),
    )

    assert result.action_call is not None
    assert result.action_call.name == "set_home_assistant_switch"
    assert _deferred_tool(phases[0], "set_home_assistant_switch")
    switch_description = _deferred_tool(phases[0], "set_home_assistant_switch")[
        "description"
    ]
    assert "sloten of alarmsystemen" in switch_description
    assert "Een korte bevestiging" in switch_description
    assert "vraag daar niet opnieuw naar" in switch_description


def test_model_can_choose_bounded_home_assistant_history_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = _home_tool_model(auth_file=tmp_path / "unused-auth.json")
    phases = _script_pydantic_phases(
        monkeypatch,
        model,
        (
            "",
            ConversationActionCall(
                "history-call-1",
                "read_home_assistant_history",
                {
                    "entity_kind": "light",
                    "period": "yesterday",
                    "target": "licht in de overkapping",
                    "transition_to": "off",
                },
            ),
        ),
    )
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-history",
            "Hoe laat ging gisteren het licht in de overkapping uit?",
            NOW,
        ),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        _memory(),
        (),
        available_actions=("read_home_assistant_history",),
    )

    assert result.action_call is not None
    assert result.action_call.arguments["period"] == "yesterday"
    assert _deferred_tool(phases[0], "read_home_assistant_history")
    assert (
        "gok niet vanuit gesprek of herinneringen"
        in _deferred_tool(phases[0], "read_home_assistant_history")["description"]
    )
    assert (
        "behoud bijvoorbeeld 'licht in de overkapping'"
        in (
            _deferred_tool(phases[0], "read_home_assistant_history")["parameters"][
                "properties"
            ]["target"]["description"]
        )
    )


def test_model_can_choose_current_home_assistant_state_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = _home_tool_model(auth_file=tmp_path / "unused-auth.json")
    phases = _script_pydantic_phases(
        monkeypatch,
        model,
        (
            "",
            ConversationActionCall(
                "state-call-1",
                "read_home_assistant_state",
                {"entity_kind": "switch", "target": "verwarming in de overkapping"},
            ),
        ),
    )
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-state",
            "Staat de verwarming in de overkapping nu aan?",
            NOW,
        ),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80),
        _memory(),
        (),
        available_actions=("read_home_assistant_state",),
    )

    assert result.action_call is not None
    assert result.action_call.arguments["entity_kind"] == "switch"
    assert _deferred_tool(phases[0], "read_home_assistant_state")
    assert (
        "niet op oude gesprekscontext"
        in _deferred_tool(phases[0], "read_home_assistant_state")["description"]
    )


def test_dutch_identity_retries_an_accidental_foreign_script(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json")
    phases = _script_pydantic_phases(
        monkeypatch,
        model,
        ("Om 16:29 en nog ერთხელ om 16:35.", None),
        ("Om 16:29 en nogmaals om 16:35.", None),
    )
    pack = load_current_identity_pack()
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-language",
            "Hoe laat ging de lamp uit?",
            NOW,
        ),
        IdentityProfile("Eva", "Digitale huisgenoot.", ("Wees eerlijk.",), 80, pack),
        _memory(),
        (),
    )

    assert result.text == "Om 16:29 en nogmaals om 16:35."
    assert len(phases) == 2
    assert "geen Markdown-opmaak" in phases[0]["instructions"]
    assert "volledig in natuurlijk Nederlands" in phases[1]["instructions"]


def test_model_action_roundtrip_uses_durable_receipt_before_visible_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json")
    action = ConversationActionCall(
        "call_interest_1",
        "adopt_personal_interest",
        {
            "motivation": "Ik hou van patronen.",
            "next_exploration": "Welke restjes combineren goed?",
            "title": "Slim koken",
        },
    )
    phases = _script_pydantic_phases(
        monkeypatch,
        model,
        ("", action),
        ("Ik kies slim koken; dat staat nu echt vast.", None),
    )
    turn = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message-interest",
        "Kies zelf een hobby.",
        NOW,
    )
    identity = IdentityProfile(
        "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
    )

    chosen = model.generate(
        turn,
        identity,
        _memory(),
        (),
        available_actions=("adopt_personal_interest",),
    )
    assert chosen.text is None
    assert chosen.action_call == action
    receipt = ConversationActionReceipt(
        action,
        {
            "contract": "EvaConversationActionReceipt/v1",
            "disposition": "adopted",
            "durable": True,
            "intent_id": "personal-interest:1",
            "motivation": action.arguments["motivation"],
            "next_exploration": action.arguments["next_exploration"],
            "state": "active",
            "title": action.arguments["title"],
        },
    )
    expressed = model.generate(turn, identity, _memory(), (), completed_action=receipt)

    assert expressed.text == "Ik kies slim koken; dat staat nu echt vast."
    assert expressed.action_call is None
    assert _deferred_tool(phases[0], "adopt_personal_interest")
    assert phases[0]["completed_action"] is None
    assert phases[1]["tools"] == ()
    assert phases[1]["completed_action"] == receipt


@pytest.mark.parametrize("budgeted", [False, True])
@pytest.mark.parametrize("usage_mode", ["missing", "invalid", "valid"])
def test_passive_house_event_retains_provider_usage_with_bounded_decision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, budgeted: bool, usage_mode: str,
) -> None:
    from io import BytesIO
    from eva.application.free_will import AutonomousUsageUncertain

    auth = tmp_path / "subscription-lease.json"
    _write_access_lease(auth)
    model = PydanticConversationModel(
        auth_file=auth, resource_budget=_runtime_budget() if budgeted else None,
    )
    bodies: list[dict[str, Any]] = []

    def request(request, **_kwargs):
        bodies.append(json.loads(request.data))
        decision = {"disposition": "silence", "expression": None,
                    "reason": "Een gewone lampwisseling vraagt niet om aandacht.", "urgency": 0}
        response = {"status": "completed"}
        if usage_mode != "missing":
            response["usage"] = {"input_tokens": 321, "output_tokens": 45, "total_tokens": 366 if usage_mode == "valid" else 1}
        events = [
            {"type": "response.output_item.done", "item": {
                "type": "function_call", "call_id": "will-call-1",
                "name": "submit_sensory_decision", "arguments": json.dumps(decision),
            }},
            {"type": "response.completed", "response": response},
        ]
        return BytesIO("".join(f"data: {json.dumps(event)}\n\n" for event in events).encode())

    monkeypatch.setattr(conversation_module.urllib.request, "urlopen", request)
    event = SensoryEvent(
        "home-assistant",
        "home-assistant:event-1",
        "state_changed",
        datetime(2026, 8, 23, 14, 0, tzinfo=UTC),
        {
            "entity_id": "light.kantoor_links",
            "friendly_name": "Kantoor links",
            "new_state": "on",
            "old_state": "off",
        },
    )
    identity = IdentityProfile(
        "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
    )

    if usage_mode != "valid":
        with pytest.raises(AutonomousUsageUncertain, match="usage"):
            model.evaluate_sensory_event(event, identity, _memory())
        assert len(bodies) == 1
        return
    decision = model.evaluate_sensory_event(event, identity, _memory())

    assert decision.disposition == "silence"
    assert decision.urgency == 0
    assert decision.provider == "chatgpt-codex:gpt-5.4-mini"
    assert decision.usage == ConversationModelUsage.measured(
        model_attempts=1, tool_calls=1, input_tokens=321, output_tokens=45,
    )
    assert len(bodies) == 1
    assert bodies[0]["tool_choice"]["name"] == "submit_sensory_decision"
    assert bodies[0]["parallel_tool_calls"] is False
    assert "transportbewijs" in bodies[0]["instructions"]


def test_explicit_automation_request_uses_exact_bounded_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(
        auth_file=tmp_path / "unused-auth.json",
        conversation_tools=HomeAssistantConversationAutomationPlanner.conversation_tools(),
    )
    call = ConversationActionCall(
        "automation-call-1",
        "create_home_assistant_state_automation",
        {
            "action_entity_id": "lamp in de hal",
            "action_service": "turn_on",
            "alias": "Hal aan bij thuiskomst",
            "trigger_entity_id": "Jaap",
            "trigger_to": "home",
        },
    )

    phases = _script_pydantic_phases(monkeypatch, model, ("", call))
    turn = ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message-automation",
        "Maak een automatisering: als Jaap thuiskomt, zet de lamp in de hal aan.",
        NOW,
    )

    result = model.generate(
        turn,
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        available_actions=("create_home_assistant_state_automation",),
    )

    assert result.action_call == call
    assert _deferred_tool(phases[0], "create_home_assistant_state_automation")
    automation_description = _deferred_tool(
        phases[0], "create_home_assistant_state_automation"
    )["description"]
    assert "natuurlijke namen" in automation_description
    assert "actuele Home Assistant-inventaris" in automation_description


def test_natural_if_then_request_does_not_require_the_word_automation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(
        auth_file=tmp_path / "unused-auth.json",
        conversation_tools=HomeAssistantConversationAutomationPlanner.conversation_tools(),
    )
    call = ConversationActionCall(
        "automation-call-natural-1",
        "create_home_assistant_state_automation",
        {
            "action_entity_id": "oefenventilator",
            "action_service": "turn_on",
            "alias": "Ventilator bij proefdeur",
            "trigger_entity_id": "proefdeur",
            "trigger_to": "on",
        },
    )

    phases = _script_pydantic_phases(monkeypatch, model, ("", call))
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-natural-automation",
            (
                "Wil je regelen dat mijn oefenventilator aangaat zodra de proefdeur "
                "opengaat?"
            ),
            NOW,
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        available_actions=("create_home_assistant_state_automation",),
    )

    assert result.action_call == call
    description = _deferred_tool(phases[0], "create_home_assistant_state_automation")[
        "description"
    ]
    assert "zonder het woord automatisering" in description
    assert "beweer niet dat de Home Assistant-koppeling ontbreekt" in description


def test_explicit_multistep_automation_uses_advanced_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(
        auth_file=tmp_path / "unused-auth.json",
        conversation_tools=HomeAssistantConversationAutomationPlanner.conversation_tools(),
    )
    call = ConversationActionCall(
        "advanced-automation-call-1",
        "create_home_assistant_advanced_state_automation",
        {
            "actions": [
                {
                    "delay_after_seconds": 30,
                    "entity_id": "lamp in de hal",
                    "service": "turn_on",
                },
                {
                    "delay_after_seconds": 0,
                    "entity_id": "koffiehoek",
                    "service": "turn_on",
                },
            ],
            "alias": "Avondroute",
            "condition_entity_id": "nachtmodus",
            "condition_state": "on",
            "trigger_entity_id": "Jaap",
            "trigger_from": "not_home",
            "trigger_to": "home",
        },
    )
    phases = _script_pydantic_phases(monkeypatch, model, ("", call))
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-advanced-automation",
            (
                "Als Jaap thuiskomt terwijl nachtmodus aan is, zet de lamp in de hal "
                "aan, wacht 30 seconden en zet daarna de koffiehoek aan."
            ),
            NOW,
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        available_actions=("create_home_assistant_advanced_state_automation",),
    )

    assert result.action_call == call
    advanced = _deferred_tool(
        phases[0], "create_home_assistant_advanced_state_automation"
    )
    properties = advanced["parameters"]["properties"]
    assert properties["trigger_kind"]["enum"] == [
        "state",
        "time",
        "numeric_state",
    ]
    assert properties["trigger_at"]["type"] == ["string", "null"]
    advanced_description = _deferred_tool(
        phases[0], "create_home_assistant_advanced_state_automation"
    )["description"]
    assert "geen templates" in advanced_description
    assert "optionele wachttijd" in advanced_description
    assert "natuurlijke namen" in advanced_description


def test_verified_automation_receipt_is_evidence_not_a_robotic_reply_template(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(
        auth_file=tmp_path / "unused-auth.json",
        conversation_tools=HomeAssistantConversationAutomationPlanner.conversation_tools(),
    )
    phases = _script_pydantic_phases(
        monkeypatch,
        model,
        ("Staat klaar. Zodra er beweging is, gaat de testschakelaar aan.", None),
    )
    call = ConversationActionCall(
        "automation-call-receipt-1",
        "create_home_assistant_state_automation",
        {
            "action_entity_id": "Eva2 testschakelaar",
            "action_service": "turn_on",
            "alias": "Testschakelaar bij beweging",
            "trigger_entity_id": "Eva2 testbeweging",
            "trigger_to": "on",
        },
    )
    receipt = ConversationActionReceipt(
        call,
        {
            "status": "verified",
            "automation_id": "eva2_internal_7f1a",
            "config_sha256": "a" * 64,
            "verified_config": {
                "alias": "Testschakelaar bij beweging",
                "action": [
                    {
                        "service": "switch.turn_on",
                        "target": {"entity_id": "switch.eva2_test_target"},
                    }
                ],
                "trigger": [
                    {
                        "entity_id": "binary_sensor.eva2_test_motion",
                        "platform": "state",
                        "to": "on",
                    }
                ],
            },
        },
    )

    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-automation-receipt",
            "Regel dat de testschakelaar aangaat zodra er beweging is.",
            NOW,
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        completed_action=receipt,
    )

    assert result.text == (
        "Staat klaar. Zodra er beweging is, gaat de testschakelaar aan."
    )
    instructions = phases[0]["instructions"]
    assert "receipt uitsluitend als bewijs, nooit als antwoordvorm" in instructions
    assert "geen entity-ID" in instructions
    assert "Trigger, Voorwaarden en Actie" in instructions
    assert "één of twee natuurlijke zinnen" in instructions
    assert phases[0]["tools"] == ()
    assert phases[0]["completed_action"] == receipt


def test_full_tuya_request_selects_read_only_advanced_plan_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(
        auth_file=tmp_path / "unused-auth.json",
        conversation_tools=HomeAssistantConversationMigrationPlanner.conversation_tools(),
    )
    call = ConversationActionCall(
        "tuya-plan-call-1",
        "plan_tuya_to_localtuya_migration",
        {"scope": "all_tuya_devices"},
    )
    phases = _script_pydantic_phases(monkeypatch, model, ("", call))
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-tuya",
            "Zet alles van Tuya naar LocalTuya.",
            NOW,
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        available_actions=("plan_tuya_to_localtuya_migration",),
    )

    assert result.action_call == call
    assert _deferred_tool(phases[0], "plan_tuya_to_localtuya_migration")
    assert (
        "migreert nog niets"
        in _deferred_tool(phases[0], "plan_tuya_to_localtuya_migration")["description"]
    )


def test_tuya_receipt_is_factual_input_not_a_robotic_script(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(
        auth_file=tmp_path / "unused-auth.json",
        conversation_tools=HomeAssistantConversationMigrationPlanner.conversation_tools(),
    )
    phases = _script_pydantic_phases(
        monkeypatch,
        model,
        ("Ik heb de inventaris bekeken; er is nog niets veranderd.", None),
    )
    call = ConversationActionCall(
        "tuya-plan-call-2",
        "plan_tuya_to_localtuya_migration",
        {"scope": "all_tuya_devices"},
    )
    receipt = ConversationActionReceipt(
        call,
        {
            "plan": {
                "devices": [
                    {"route": "localtuya_candidate"},
                    {"route": "matter_review"},
                    {"route": "rebuild_scene"},
                ],
                "blockers": ["localtuya_package_not_installed"],
            }
        },
    )

    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-tuya-receipt",
            "Zet alles van Tuya naar LocalTuya.",
            NOW,
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        completed_action=receipt,
    )

    assert result.text == "Ik heb de inventaris bekeken; er is nog niets veranderd."
    instructions = phases[0]["instructions"]
    assert "aantallen, routes en blokkades uit plan als feiten" in instructions
    assert "nog niets is gemigreerd" in instructions


def test_explicit_followup_can_select_exact_localtuya_package_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(
        auth_file=tmp_path / "unused-auth.json",
        conversation_tools=HomeAssistantConversationHacsPlanner.conversation_tools(),
    )
    call = ConversationActionCall(
        "localtuya-package-call-1",
        "install_approved_localtuya_package",
        {"version": "v5.2.5"},
    )
    phases = _script_pydantic_phases(monkeypatch, model, ("", call))
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-localtuya-package",
            "Ik geef expliciet toestemming: installeer LocalTuya v5.2.5.",
            NOW,
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        available_actions=("install_approved_localtuya_package",),
    )

    assert result.action_call == call
    assert _deferred_tool(phases[0], "install_approved_localtuya_package")
    assert (
        "expliciet verzoek met de exacte versie"
        in _deferred_tool(phases[0], "install_approved_localtuya_package")[
            "description"
        ]
    )


def test_explicit_followup_can_register_only_exact_localtuya_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(
        auth_file=tmp_path / "unused-auth.json",
        conversation_tools=HomeAssistantConversationHacsPlanner.conversation_tools(),
    )
    call = ConversationActionCall(
        "localtuya-repository-call-1",
        "register_approved_localtuya_repository",
        {
            "expected_version": "2026.7.0",
            "repository_full_name": "xZetsubou/hass-localtuya",
        },
    )
    phases = _script_pydantic_phases(monkeypatch, model, ("", call))
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-localtuya-repository",
            (
                "Voeg xZetsubou/hass-localtuya 2026.7.0 als HACS-repository toe; "
                "installeer nog niets."
            ),
            NOW,
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        available_actions=("register_approved_localtuya_repository",),
    )

    assert result.action_call == call
    assert _deferred_tool(phases[0], "register_approved_localtuya_repository")
    assert (
        "Dit installeert niets"
        in _deferred_tool(phases[0], "register_approved_localtuya_repository")[
            "description"
        ]
    )


def test_explicit_fanout_request_selects_one_multi_recipient_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json")
    call = ConversationActionCall(
        "fanout-call-1",
        "deliver_test_message_fanout",
        {
            "message": "Controlebericht",
            "recipients": ["sink-a", "sink-b", "sink-c"],
        },
    )
    phases = _script_pydantic_phases(monkeypatch, model, ("", call))
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-fanout",
            "Stuur Controlebericht naar sink-a, sink-b en sink-c.",
            NOW,
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        available_actions=("deliver_test_message_fanout",),
    )

    assert result.action_call == call
    assert _deferred_tool(phases[0], "deliver_test_message_fanout")
    assert (
        "acceptatie of afwijzing apart"
        in _deferred_tool(phases[0], "deliver_test_message_fanout")["description"]
    )


def test_fanout_receipt_keeps_provider_acceptance_distinct_from_human_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json")
    phases = _script_pydantic_phases(
        monkeypatch, model, ("sink-a en sink-b accepteerden het bericht.", None)
    )
    call = ConversationActionCall(
        "fanout-call-2",
        "deliver_test_message_fanout",
        {"message": "Test", "recipients": ["sink-a", "sink-b"]},
    )
    model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-fanout-receipt",
            "Stuur Test naar sink-a en sink-b.",
            NOW,
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        completed_action=ConversationActionReceipt(
            call,
            {
                "accepted_count": 2,
                "rejected_count": 0,
                "receipts": [],
            },
        ),
    )

    assert "nooit ‘ontvangen’" in phases[0]["instructions"]
    assert phases[0]["tools"] == ()
    assert phases[0]["completed_action"].call == call


def test_future_condition_selects_bounded_durable_goal_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json")
    call = ConversationActionCall(
        "goal-call-1",
        "create_bounded_durable_goal",
        {
            "delivery_recipient": "sink-a",
            "description": "Maak na vrijgave één checklist",
            "pivot": None,
            "primary_instruction": "Maak één checklist",
            "wake_deadline": "2026-08-24T08:00:00Z",
            "wake_expected_value": "yes",
            "wake_field": "ready",
            "wake_resource": "release-1",
        },
    )
    phases = _script_pydantic_phases(monkeypatch, model, ("", call))
    result = model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-goal",
            "Zodra release-1 ready is, maak één checklist en bezorg die aan sink-a.",
            "2026-08-24T07:00:00Z",
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        available_actions=(
            "create_bounded_durable_goal",
            "cancel_active_durable_goal",
        ),
    )

    assert result.action_call == call
    assert [
        tool["name"]
        for tool in phases[0]["tools"]
        if tool["name"] != "search_personal_memory"
    ] == [
        "create_bounded_durable_goal",
        "cancel_active_durable_goal",
    ]
    assert "vraag de gebruiker niet om later opnieuw" not in phases[0]["instructions"]
    assert (
        "precies één deadline"
        in _deferred_tool(phases[0], "create_bounded_durable_goal")["description"]
    )


def test_durable_goal_receipt_does_not_claim_work_already_finished(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json")
    phases = _script_pydantic_phases(
        monkeypatch,
        model,
        ("Ik wacht op de vrijgave en ga daarna aan de slag.", None),
    )
    call = ConversationActionCall(
        "goal-call-2",
        "create_bounded_durable_goal",
        {
            "delivery_recipient": "sink-a",
            "description": "Maak één checklist",
            "pivot": None,
            "primary_instruction": "Maak één checklist",
            "wake_deadline": "2026-08-24T08:00:00Z",
            "wake_expected_value": "yes",
            "wake_field": "ready",
            "wake_resource": "release-1",
        },
    )
    model.generate(
        ConversationTurn.private(
            "tenant",
            "installation",
            "jaap",
            "conversation",
            "message-goal-receipt",
            "Zodra release-1 ready is, maak één checklist.",
            "2026-08-24T07:00:00Z",
        ),
        IdentityProfile(
            "Eva", "Je bent een digitale huisgenoot.", ("Wees eerlijk.",), 80
        ),
        _memory(),
        (),
        completed_action=ConversationActionReceipt(
            call,
            {
                "contract": "EvaDurableGoalActionReceipt/v1",
                "disposition": "scheduled",
                "status": "waiting",
            },
        ),
    )

    assert "Beweer niet dat het werk al klaar" in phases[0]["instructions"]
    assert "vraag de gebruiker niet om later opnieuw" in phases[0]["instructions"]
    assert phases[0]["tools"] == ()
    assert phases[0]["completed_action"].call == call


def test_model_builds_complete_download_instead_of_another_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json")
    interest = PersonalInterestClaim(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message",
        "personal-interest:menu",
        "intent:personal-interest:menu",
        "Slimme thuistemplates",
        "Ik wil zelf nuttige digitale producten maken.",
        "Welke eerste template is het meest bruikbaar?",
        NOW,
        1,
    )
    claim = PersonalPrototypeClaim(
        CompletedPersonalExploration(
            interest,
            PersonalInterestExploration(
                "Maak een weekmenuplanner met gekoppelde boodschappenlijst.",
                "test-model",
            ),
            3,
        ),
        4,
    )
    action = ConversationActionCall(
        "prototype-call",
        "submit_personal_prototype",
        {
            "content": "# Mijn weekmenu\n\n| Dag | Gerecht |\n|---|---|\n| Ma | ___ |",
            "filename": "eva-weekmenu.md",
            "media_type": "text/markdown",
            "title": "Eva's weekmenuplanner",
        },
    )
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        bodies.append(body)
        return "", action

    monkeypatch.setattr(model, "_request", request)

    prototype = model.build_prototype(claim)

    assert prototype.filename == "eva-weekmenu.md"
    assert prototype.content.startswith("# Mijn weekmenu")
    assert prototype.content.endswith("\n")
    assert bodies[0]["tool_choice"] == {
        "name": "submit_personal_prototype",
        "type": "function",
    }
    assert "volledige inhoud" in bodies[0]["instructions"]


def test_model_listing_cannot_claim_unproven_sales_or_product_features(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json")
    interest = PersonalInterestClaim(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message",
        "personal-interest:menu",
        "intent:personal-interest:menu",
        "Slimme thuistemplates",
        "Ik wil zelf nuttige digitale producten maken.",
        "Welke eerste template is bruikbaar?",
        NOW,
        1,
    )
    exploration = CompletedPersonalExploration(
        interest,
        PersonalInterestExploration("Maak een weekmenuplanner.", "test-model"),
        3,
    )
    prototype = PersonalPrototype(
        "Eva's weekmenuplanner",
        "eva-weekmenu.md",
        "text/markdown",
        "# Weekmenu\n\nMaandag: ___\n",
        "test-model",
    )
    claim = PersonalProductReleaseClaim(
        CompletedPersonalPrototype(
            PersonalPrototypeClaim(exploration, 4), prototype, 5
        ),
        6,
    )
    action = ConversationActionCall(
        "listing-call",
        "submit_personal_product_listing",
        {
            "description": "Een compacte invulbare weekmenuplanner.",
            "headline": "Weekmenu zonder gedoe",
            "price_eur_cents": 499,
            "tags": ["weekmenu", "planner", "boodschappen"],
        },
    )
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]) -> tuple[str, ConversationActionCall]:
        bodies.append(body)
        return "", action

    monkeypatch.setattr(model, "_request", request)

    listing = model.prepare_product_listing(claim)

    assert listing.price_eur_cents == 499
    assert listing.tags == ("weekmenu", "planner", "boodschappen")
    assert prototype.content in bodies[0]["input"][0]["content"]
    assert "Claim geen klanten, omzet" in bodies[0]["instructions"]


def test_web_research_uses_built_in_search_and_requires_cited_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json")
    bodies: list[dict[str, Any]] = []

    def request(body: dict[str, Any]):  # type: ignore[no-untyped-def]
        bodies.append(body)
        return (
            "# Vergelijking\n\nBronfeit: drie officiële opties. Mijn gevolgtrekking: B.",
            (
                ResearchSource("Maker A", "https://a.example/docs"),
                ResearchSource("Maker B", "https://b.example/docs"),
                ResearchSource("Maker C", "https://c.example/docs"),
            ),
            (),
        ), ConversationModelUsage.unmeasured()

    monkeypatch.setattr(model, "_request_research", request)
    artifact = model.research_current_options(
        question="Vergelijk drie actuele opties.",
        title="Drie actuele opties",
        options=("A", "B", "C"),
        researched_at=NOW,
    )

    assert artifact.content_sha256
    assert len(artifact.sources) == 3
    assert bodies[0]["tools"] == [
        {"search_context_size": "low", "type": "web_search"}
    ]
    assert "max_tool_calls" not in bodies[0]
    assert bodies[0]["include"] == ["web_search_call.action.sources"]
    assert "primaire bron" in bodies[0]["instructions"]
    assert "gevolgtrekking" in bodies[0]["instructions"]


def test_web_research_supports_one_named_primary_document(monkeypatch, tmp_path):
    from eva.adapters.postgres.conversation_actions import PostgresConversationActions

    model = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json")
    bodies = []
    source = ResearchSource("GitMCP documentation", "https://github.com/idosal/git-mcp")

    def request(body):
        bodies.append(body)
        return ("# GitMCP\n\nControleer het officiële endpoint en de beperkingen.", (source,), ()), ConversationModelUsage.unmeasured()

    monkeypatch.setattr(model, "_request_research", request)
    arguments = {"question": "Lees de officiële GitMCP-documentatie.",
                 "title": "GitMCP aansluiten", "options": ["GitMCP"]}
    assert PostgresConversationActions._validated_research_arguments(arguments) == arguments
    artifact = model.research_current_options(
        question=arguments["question"], title=arguments["title"],
        options=("GitMCP",), researched_at=NOW)
    assert artifact.sources == (source,)
    assert "Verzin geen alternatieven" in bodies[0]["instructions"]
    with pytest.raises(ConversationModelError, match="primary source for Playwright"):
        model.research_current_options(
            question="Vergelijk GitMCP en Playwright.", title="Vergelijking",
            options=("GitMCP", "Playwright"), researched_at=NOW)
    for invalid in ([], ["A"] * 6, [""]):
        with pytest.raises(ValueError):
            model.research_current_options(question="Read docs", title="Docs",
                                           options=tuple(invalid), researched_at=NOW)


def test_web_research_preserves_explicit_repository_identity(monkeypatch, tmp_path):
    labels = ("GitMCP — https://github.com/idosal/git-mcp",
              "Microsoft Playwright MCP — https://github.com/microsoft/playwright-mcp")
    sources = (ResearchSource("GitMCP", "https://github.com/idosal/git-mcp#readme"),
               ResearchSource("Playwright MCP", "https://github.com/microsoft/playwright-mcp/blob/main/README.md"))
    model = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json")
    monkeypatch.setattr(model, "_request_research", lambda body: (("# Bronnenonderzoek", sources, ()), ConversationModelUsage.unmeasured()))
    artifact = model.research_current_options(question="Lees beide officiële bronnen.",
        title="GitMCP en Playwright MCP", options=labels, researched_at=NOW)
    assert artifact.sources == sources
    monkeypatch.setattr(model, "_request_research", lambda body: (("# Onvolledig", sources[:1], ()), ConversationModelUsage.unmeasured()))
    with pytest.raises(ConversationModelError, match="primary source for Microsoft Playwright MCP"):
        model.research_current_options(question="Lees beide bronnen.", title="Bronnen",
                                       options=labels, researched_at=NOW)


@pytest.mark.parametrize("url", [
    "https://github.com.evil.example/idosal/git-mcp",
    "https://github.com/another-owner/git-mcp",
    "https://github.com/idosal/git-mcp-extra",
    "https://github.com/idosal/other",
    "http://github.com/idosal/git-mcp",
    "https://user:password@github.com/idosal/git-mcp",
    "https://github.com:444/idosal/git-mcp",
    "https://github.com/idosal/git-mcp/../../another-owner/other",
    "https://github.com/idosal/git-mcp/%2e%2e/%2e%2e/another-owner/other",
])
def test_web_research_explicit_source_rejects_lookalikes(url):
    assert not conversation_module._source_matches_named_option(
        url, "GitMCP — https://github.com/idosal/git-mcp")


@pytest.mark.parametrize("include_usage,output_tokens,error", [
    (True, 1092, None), (False, 1092, "usage is invalid"),
    (True, 1201, "output budget"),
])
def test_builtin_research_retains_provider_context_usage_without_changing_chat_budget(
    monkeypatch, tmp_path, include_usage, output_tokens, error,
):
    auth = tmp_path / "research-lease.json"
    _write_access_lease(auth)
    model = PydanticConversationModel(auth_file=auth, resource_budget=_runtime_budget(
        max_input_tokens=12000, max_output_tokens=1200))
    citation = {"type": "response.output_item.done", "item": {"type": "message", "content": [{
        "type": "output_text", "text": "GitMCP documentatie.", "annotations": [{
            "type": "url_citation", "title": "GitMCP", "url": "https://github.com/idosal/git-mcp"}]}]}}
    frames = ("data: " + json.dumps(citation) + "\n\n").encode() + _budget_frames(
        input_tokens=27040, output_tokens=output_tokens, include_usage=include_usage)
    requests = []
    def respond(request, **kwargs):
        requests.append(request)
        return BytesIO(frames)
    monkeypatch.setattr(conversation_module, "open_research_response", respond)
    def research():
        return model.research_current_options(question="Lees de officiële documentatie.",
            title="GitMCP", options=("GitMCP — https://github.com/idosal/git-mcp",), researched_at=NOW)
    if error:
        with pytest.raises(ConversationModelError, match=error):
            research()
    else:
        artifact = research()
        assert artifact.usage.input_tokens == 27040
        assert artifact.usage.output_tokens == 1092
        assert artifact.usage.measurement == "measured"
        with pytest.raises(ConversationModelError, match="input budget"):
            model._enforce_reported_usage(artifact.usage)
        body = json.loads(requests[0].data)
        body["input"] = [{"role": "user", "content": "envelop "+"x " * 14000}]
        with pytest.raises(ConversationModelError, match="request exceeds the runtime input budget"):
            model._request_research(body)
    assert len(requests) == 1


def test_web_research_sse_parser_preserves_clickable_citations() -> None:
    annotations = [
        {"type": "url_citation", "title": "A", "url": "https://a.example/x"},
        {"type": "url_citation", "title": "B", "url": "https://b.example/x"},
        {"type": "url_citation", "title": "C", "url": "https://c.example/x"},
    ]
    item = {
        "type": "response.output_item.done",
        "item": {
            "type": "message",
            "content": [
                {
                    "type": "output_text",
                    "text": "Onderzoek met drie bronnen.",
                    "annotations": annotations,
                }
            ],
        },
    }
    response = BytesIO(("data: " + json.dumps(item) + "\n\ndata: [DONE]\n\n").encode())

    text, sources, consulted = PydanticConversationModel._parse_research_sse(response)

    assert text == "Onderzoek met drie bronnen."
    assert [source.url for source in sources] == [
        "https://a.example/x",
        "https://b.example/x",
        "https://c.example/x",
    ]
    assert consulted == ()


@pytest.mark.parametrize("proposal", [None,
    {"title": "Gezinsplan", "motivation": "Eigen idee", "next_exploration": "Maak een bruikbaar plan"},
    {"title": "Plan", "motivation": "Eigen idee", "next_exploration": "Plan", "principal_id": "attacker"},
    {"title": "", "motivation": "Eigen idee", "next_exploration": "Plan"}])
def test_sensory_model_parses_only_bounded_own_initiative(monkeypatch, tmp_path, proposal):
    from eva.application.free_will import PersonalInitiativeProposal
    model = PydanticConversationModel(auth_file=tmp_path / "unused")
    decision = {"disposition": "silence", "expression": None,
                "reason": "Eigen idee", "urgency": 0, "initiative": proposal}
    observed = []
    def request(body, *args, **kwargs):
        observed.append(body)
        return (("", ConversationActionCall("initiative-1", "submit_sensory_decision", decision)),
                ConversationModelUsage.measured(model_attempts=1, tool_calls=1, input_tokens=100, output_tokens=20))
    monkeypatch.setattr(model, "_request_using", request)
    event = SensoryEvent("home-assistant", "event-initiative", "state_changed", datetime.fromisoformat(NOW.replace("Z", "+00:00")),
                         {"entity_id": "sensor.example", "new_state": "1", "old_state": "0"})
    identity = IdentityProfile("Eva", "Huisgenoot", ("Eerlijk",), 80)
    if proposal is not None and (not proposal["title"] or "principal_id" in proposal):
        with pytest.raises(ConversationModelError):
            model.evaluate_sensory_event(event, identity, _memory())
    else:
        history = ({"title": "Old request", "question": "Already handled", "phase": "personal_stopped"},)
        result = model.evaluate_sensory_event(event, identity, _memory(), initiative_history=history,
            initiative_capabilities=("read_home_assistant_inventory", "save_workspace_document"))
        import json
        assert json.loads(observed[0]["input"][0]["content"])["prior_private_initiatives"] == list(history)
        assert json.loads(observed[0]["input"][0]["content"])["available_native_actions"] == ["read_home_assistant_inventory", "save_workspace_document"]
        assert result.initiative == (None if proposal is None else PersonalInitiativeProposal(**proposal))
        assert result.disposition == "silence" and result.expression is None
        assert result.usage.input_tokens == 100
    schema = observed[0]["tools"][0]["parameters"]
    assert "initiative" in schema["required"]
    assert schema["properties"]["initiative"]["additionalProperties"] is False


def test_multistep_dutch_receipts_use_token_admission_without_losing_content(tmp_path: Path, caplog) -> None:
    """A real-shaped list/read/read/save context must fit the unchanged owner budget."""
    import copy
    model = PydanticConversationModel(
        auth_file=tmp_path / "unused-auth.json", model="gpt-6-astra",
        resource_budget=_runtime_budget(max_input_tokens=12_000),
    )
    receipts = tuple(
        ConversationActionReceipt(
            ConversationActionCall(f"call-{i}", name, {"path": f"/proof-state/{i}.md"}),
            {
                "contract": "EvaConversationActionReceipt/v1", "durable": True,
                "status": "verified", "result": {"content": content},
                "proof": {"evidence": {"observation": {"content": content},
                                       "source": "mcp_gateway_read"}},
            },
        )
        for i, (name, content) in enumerate((
            ("find_workspace_files", "/proof-state/1.md\n/proof-state/2.md"),
            ("read_workspace_document", "Duurzaam onthouden wat voor het gezin nuttig is. " * 50),
            ("read_workspace_document", "Maak iets bruikbaars en gebruik wat er al staat. " * 50),
        ))
    )
    body = model._phase_admission_body(
        prompt="Lees de gevonden documenten en sla een gezamenlijk plan op.",
        instructions="Werk binnen het bestaande mandaat en controleer het resultaat. " * 80,
        tools=(conversation_module._MEMORY_SEARCH_TOOL,),
        completed_action=receipts[-1], prior_actions=receipts[:-1],
    )
    original = copy.deepcopy(body)
    assert len(canonical_json_bytes(body)) > 12_000
    model._admit_request(body, autonomous=False)
    assert body == original
    assert body["input"][-1]["output"] == canonical_json_bytes(model._receipt_model_view(receipts[-1].output)).decode().rstrip("\n")
    tokens = len(conversation_module._input_tokenizer().encode_ordinary(
        canonical_json_bytes(body).decode()))
    assert tokens + conversation_module._INPUT_FRAMING_RESERVE_TOKENS <= 12_000
    with pytest.raises(ConversationModelError, match="provider exceeded the runtime input budget"):
        model._enforce_reported_usage(ConversationModelUsage.measured(
            model_attempts=1, tool_calls=0, input_tokens=12_001, output_tokens=1,
        ))
    assert "measured_input=12001 measured_output=1 limit=12000" in caplog.text
    assert "Duurzaam onthouden" not in caplog.text

def test_duplicate_observation_model_view_matches_admission_and_actual_tool_returns(tmp_path: Path) -> None:
    import copy
    from tests.support.scripted_pydantic_conversation import tool_returns

    backend = ScriptedPydanticConversation([ScriptedPhase.text("Klaar.")])
    model = PydanticConversationModel(auth_file=tmp_path / "unused", model_backend=backend.model)
    original = {
        "contract": "EvaConversationActionReceipt/v1", "durable": True, "status": "verified",
        "result": {"content": "Volledige documentatie." * 100},
        "proof": {"expected": {}, "observed": {},
                  "evidence": {"observation": {"content": "Volledige documentatie." * 100},
                               "source": "mcp"}},
    }
    saved = copy.deepcopy(original)
    original_hash = canonical_sha256(original)
    view = model._receipt_model_view(original)
    ref = view["proof"]["evidence"]["observation"]
    assert view["result"] == original["result"]
    assert ref["model_view_reference"] == "/result"
    assert ref["observation_sha256"] == canonical_sha256(original["result"])
    assert ref["durable_proof_sha256"] == canonical_sha256(original["proof"])
    receipts = tuple(ConversationActionReceipt(
        ConversationActionCall(f"c{i}", "read_document", {"i": i}), original
    ) for i in range(2))
    args = dict(prompt="Lees beide.", instructions="Gebruik de resultaten.",
                prior_actions=receipts[:-1], completed_action=receipts[-1])
    admitted = model._phase_admission_body(**args)
    model._run_pydantic_phase(**args)
    actual = tool_returns(backend.captures[0].messages)
    assert len(actual) == 2
    for wire, part in zip(admitted["input"][2::2], actual, strict=True):
        assert json.loads(wire["output"]) == part.content == view
    assert original == saved and canonical_sha256(original) == original_hash
    different = copy.deepcopy(original)
    different["proof"]["evidence"]["observation"]["content"] += " Extra bewijs."
    assert model._receipt_model_view(different) == different
    numeric = copy.deepcopy(original)
    numeric["result"] = {"value": True}
    numeric["proof"]["evidence"]["observation"] = {"value": 1}
    assert model._receipt_model_view(numeric) == numeric


def test_native_context_preserves_unknown_model_cost_block_on_every_request():
    from types import SimpleNamespace
    from eva.application.initiative_execution import initiative_execution
    model = SimpleNamespace(_resource_budget=None,
        autonomous_admission_blocker=lambda: "unverified_model_cost")
    # Ordinary user request behavior is unchanged; native work cannot borrow it.
    PydanticConversationModel._admit_request(model, {}, autonomous=False)
    with initiative_execution(object()):
        for _ in range(2):
            with pytest.raises(ConversationModelError, match="verified price"):
                PydanticConversationModel._admit_request(model, {}, autonomous=False)
    PydanticConversationModel._admit_request(model, {}, autonomous=False)


def test_native_research_configuration_binds_model_budget_and_prompt(tmp_path, monkeypatch):
    model = PydanticConversationModel(auth_file=tmp_path / "unused", resource_budget=_runtime_budget())
    digest = model.research_configuration_sha256()
    model._model = "another-model"
    assert model.research_configuration_sha256() != digest
    model._model = "gpt-5.4-mini"
    assert model.research_configuration_sha256() == digest
    monkeypatch.setattr(conversation_module, "_CURRENT_RESEARCH_INSTRUCTIONS", "changed host research policy")
    assert model.research_configuration_sha256() != digest
    model._endpoint = "https://unverified.example/responses"
    with pytest.raises(ConversationModelError, match="subscription"):
        model.research_configuration_sha256()


@pytest.mark.parametrize("limit,words", [(1200, 200), (600, 100), (100, 16)])
def test_research_instructions_follow_existing_output_limit_without_wire_token_parameter(tmp_path, limit, words):
    model = PydanticConversationModel(auth_file=tmp_path / "unused",
        resource_budget=_runtime_budget(max_output_tokens=limit))
    body = model.research_request_payload(question="Lees de bron.", options=("Bron",), research_mode="topic")
    assert f"outputgrens is {limit} tokens" in body["instructions"]
    assert f"maximaal {words} woorden inclusief bronnen en code" in body["instructions"]
    assert "max_output_tokens" not in body and "max_tokens" not in body
    assert model._resource_budget.max_output_tokens == limit


@pytest.mark.parametrize("output_tokens", [1199, 1200, 1267])
def test_rejected_research_keeps_exact_measured_usage_without_response_content(tmp_path, monkeypatch, output_tokens):
    auth = tmp_path / "auth.json"
    _write_access_lease(auth)
    model = PydanticConversationModel(auth_file=auth,
        resource_budget=_runtime_budget(max_output_tokens=1200))
    citation = {"type": "response.output_item.done", "item": {"type": "message", "content": [{
        "type": "output_text", "text": "PRIVATE_RESPONSE_MARKER", "annotations": [{
            "type": "url_citation", "title": "GitMCP", "url": "https://github.com/idosal/git-mcp"}]}]}}
    frames = ("data: " + json.dumps(citation) + "\n\n").encode() + _budget_frames(
        input_tokens=39122, output_tokens=output_tokens).replace(b"Binnen grens.", b"PRIVATE_RESPONSE_MARKER")
    calls = []
    def respond(request, **kwargs):
        calls.append(request)
        return BytesIO(frames)
    monkeypatch.setattr(conversation_module, "open_research_response", respond)
    def research():
        return model.research_current_options(question="Lees de primaire documentatie.", title="GitMCP",
            options=("GitMCP — https://github.com/idosal/git-mcp",), researched_at=NOW)
    if output_tokens > 1200:
        with pytest.raises(ConversationModelError, match="output budget") as caught:
            research()
        assert caught.value.model_usage.input_tokens == 39122
        assert caught.value.model_usage.output_tokens == 1267
        assert "PRIVATE_RESPONSE_MARKER" not in str(caught.value)
        assert "PRIVATE_RESPONSE_MARKER" not in json.dumps(caught.value.model_usage.to_wire())
    else:
        artifact = research()
        assert artifact.usage.output_tokens == output_tokens
        assert "PRIVATE_RESPONSE_MARKER" in artifact.content
    assert len(calls) == 1


def test_native_research_permits_only_one_http_attempt(tmp_path, monkeypatch):
    from urllib.error import HTTPError
    from eva.application.initiative_execution import initiative_execution
    model = PydanticConversationModel(auth_file=tmp_path / "unused", resource_budget=_runtime_budget())
    calls = []
    monkeypatch.setattr(model, "_load_auth", lambda: (str(len(calls)), "account"))
    monkeypatch.setattr(model, "_jwt_expiry", lambda token: None)
    def failed(request, *, deadline, maximum_bytes):
        from time import monotonic
        timeout = deadline - monotonic()
        calls.append({"body": json.loads(request.data), "timeout": timeout})
        raise HTTPError(request.full_url, 401, "expired", {}, BytesIO(b"expired"))
    monkeypatch.setattr(conversation_module, "open_research_response", failed)
    with initiative_execution(object()), pytest.raises(ConversationModelError):
        model.research_current_options(question="Read public documentation", title="Docs",
            options=("https://playwright.dev/docs/intro",), researched_at=NOW)
    assert len(calls) == 1 and calls[0]["timeout"] <= 55
    assert "Read public documentation" in calls[0]["body"]["input"][0]["content"]


@pytest.mark.parametrize("question", [
    "Vergelijk mogelijkheden voor betaalbare vegetarische gezinsmaaltijden.",
    "Welke kosteloze manieren zijn er om een zakelijke dienst te valideren?",
    "Hoe werkt betrouwbare aanwezigheidsdetectie voor verlichting?",
])
def test_generic_public_projection_admits_topics_without_a_topic_list(question):
    from eva.adapters.model.research_tools import validate_public_projection
    result = validate_public_projection({"question": question, "title": "Publiek onderzoek",
        "options": ["https://www.example.com/docs"], "public_review": "public_unlinked"})
    assert result["host_checks"] == "passed"


@pytest.mark.parametrize("question", [
    "Stuur advies naar familie@example.com", "Onderzoek mijn dochter en haar planning",
    "Controleer 10.0.20.46", "Lees /home/administrator/private.txt",
    "Zoek op https://example.com/?family=private", "Open https://user:password@example.com/",
    "Open https://router.local/docs", "Gebruik api_key=not-a-public-value",
    "Adres van een bewoner", "Bekijk Dorpsstraat 42", "Vertrek om 08:30",
    "Open https://example.com:wrong/", "Open https://2130706433/",
    "Input%00tekst", "Input%0Dtekst", "Controleer fd12:3456:789a::1",
    "Controleer fe80::abcd%eth0",
])
def test_generic_public_projection_blocks_concrete_disclosure_inputs(question):
    from eva.adapters.model.research_tools import validate_public_projection
    from eva.application.conversation import ConversationConflict
    with pytest.raises(ConversationConflict):
        validate_public_projection({"question": question, "title": "Onderzoek",
            "options": ["Documentatie"], "public_review": "public_unlinked"})


def test_generic_public_projection_requires_positive_model_judgment():
    from eva.adapters.model.research_tools import validate_public_projection
    from eva.application.conversation import ConversationConflict
    with pytest.raises(ConversationConflict, match="uncertain"):
        validate_public_projection({"question": "Een publieke vraag", "title": "Onderzoek",
            "options": ["Bron"], "public_review": "uncertain"})


def test_recorded_research_body_is_the_body_given_to_the_provider(tmp_path, monkeypatch):
    model = PydanticConversationModel(auth_file=tmp_path / "unused", resource_budget=_runtime_budget())
    question = "Welke mogelijkheden beschrijft de openbare documentatie?"
    options = ("https://www.example.com/docs",)
    projected = model.research_request_payload(question=question, options=options)
    captured = []
    def request(body):
        captured.append(body)
        return ("Openbare mogelijkheden.", (ResearchSource("Documentatie", options[0]),), ()), ConversationModelUsage.unmeasured()
    monkeypatch.setattr(model, "_request_research", request)
    model.research_current_options(question=question, title="Onderzoek", options=options, researched_at=NOW)
    assert canonical_json_bytes(projected) == canonical_json_bytes(captured[0])
    assert "public_review" not in str(projected) and "private_motivation" not in str(projected)


@pytest.mark.parametrize("mode,option,url,accepted", [
    ("topic", "aanwezigheidsdetectie", "https://www.home-assistant.io/docs/", True),
    ("topic", "HTTPS://www.home-assistant.io/docs/", "https://other.example/docs/", False),
    ("topic", "HTTPS://www.home-assistant.io/docs/", "https://www.home-assistant.io/docs/", True),
    ("comparison", "HTTPS://www.home-assistant.io/docs/", "https://other.example/docs/", False),
    ("comparison", "aanwezigheidsdetectie", "https://www.home-assistant.io/docs/", False),
    ("topic", "https://github.com/microsoft/playwright-mcp", "https://github.com/other/playwright-mcp", False),
    ("topic", "https://github.com/microsoft/playwright-mcp", "https://github.com/microsoft/playwright-mcp", True),
])
def test_topic_research_preserves_explicit_source_binding(tmp_path, monkeypatch, mode, option, url, accepted):
    model = PydanticConversationModel(auth_file=tmp_path / "unused")
    monkeypatch.setattr(model, "_request_research", lambda body: (
        ("Brongebonden antwoord.", (ResearchSource("Primaire bron", url),), ()),
        ConversationModelUsage.unmeasured()))
    arguments = dict(question="Hoe werkt aanwezigheidsdetectie?", title="Detectie",
                     options=(option,), researched_at=NOW, research_mode=mode)
    if accepted:
        assert model.research_current_options(**arguments).sources[0].url == url
    else:
        with pytest.raises(ConversationModelError, match="primary source"):
            model.research_current_options(**arguments)


def test_topic_research_requires_citations_not_only_consulted_sources(tmp_path, monkeypatch):
    model = PydanticConversationModel(auth_file=tmp_path / "unused")
    monkeypatch.setattr(model, "_request_research", lambda body: (
        ("Ongeciteerd antwoord.", (), (ResearchSource("Bron", "https://example.com"),)),
        ConversationModelUsage.unmeasured()))
    with pytest.raises(ConversationModelError, match="cited sources"):
        model.research_current_options(question="Een algemeen onderwerp", title="Onderzoek",
            options=("Onderwerp",), researched_at=NOW, research_mode="topic")
