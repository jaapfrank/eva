import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from eva.application.conversation import ConversationActionCall, ConversationConflict, ConversationTurn
from eva.application.orchestration import ToolRequest, ToolDispatchResult
from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings
from eva.providers.telegram_contacts import (
    LIST, SEND, TelegramContacts, TelegramContactsProviderFactory, TelegramContactAuthority,
)


def fixture(monkeypatch):
    turn = ConversationTurn.private("tenant", "installation", "owner", "chat", "message",
        "Vraag het gezinscontact wat die wil eten.", "2026-10-23T16:00:00Z")
    entry = TelegramPrincipalBinding(123, "contact", ("123",), "11111111-1111-4111-8111-111111111111")
    state = {"bindings": TelegramPrincipalBindings((entry,)), "token": "123:token", "allowed": True}
    def authorize(candidate):
        if candidate != turn or not state["allowed"]:
            raise ConversationConflict("owner changed")
    provider = TelegramContacts(authority=SimpleNamespace(authorize_turn=authorize),
        load_source=lambda source: turn, bindings=lambda: state["bindings"],
        credential=lambda: state["token"], configuration_sha256="a" * 64,
        ensure_running=authorize)
    calls = []
    def api_call(api, method, payload, **kwargs):
        calls.append((method, payload))
        if method == "getChat":
            return {"id": 123, "type": "private", "first_name": "Gezinscontact"}
        return {"message_id": 77, "chat": {"id": 123, "type": "private"}, "text": payload["text"]}
    monkeypatch.setattr("eva.providers.telegram_contacts.TelegramBotApiAdapter._call", api_call)
    return provider, turn, state, calls, entry


def command(provider, turn, name, arguments):
    prepared = asyncio.run(provider.prepare(turn, ConversationActionCall("call", name, arguments)))
    return ToolRequest(thread_id="thread", operation_id="operation", checkpoint_prefix="checkpoint",
        tool_name=prepared.tool_name, arguments=prepared.arguments, expected_proof=prepared.expected_proof,
        observation_tool_name=prepared.observation_tool_name,
        observation_arguments=prepared.observation_arguments,
        tool_configuration_sha256=prepared.tool_configuration_sha256)


def send_command(provider, turn):
    return command(provider, turn, SEND, {"contact_ref": next(iter(provider._contacts())),
        "text": "Wat wil je eten?", "request_quote": turn.text})


def test_contact_discovery_does_not_read_messages_or_link_accounts(monkeypatch):
    provider, turn, state, calls, entry = fixture(monkeypatch)
    request = command(provider, turn, LIST, {})
    dispatch = asyncio.run(provider.execute(request))
    proof = asyncio.run(provider.observe(request, dispatch))
    assert proof.disposition == "verified"
    assert calls == [("getChat", {"chat_id": "123"})]
    result = dispatch.details["result"]
    assert result["conversation_content_read"] is False
    assert result["contacts"][0]["first_name"] == "Gezinscontact"
    assert state["bindings"].resolve(123, "123") == "contact"


def test_exact_recipient_and_text_receipt(monkeypatch):
    provider, turn, state, calls, entry = fixture(monkeypatch)
    request = send_command(provider, turn)
    dispatch = asyncio.run(provider.execute(request))
    proof = asyncio.run(provider.observe(request, dispatch))
    assert proof.disposition == "verified"
    assert dispatch.provider_ref == "telegram:123:77"
    assert dispatch.details["result"]["read_by_recipient"] is False
    assert calls == [("sendMessage", {"chat_id": "123", "disable_notification": True,
                                    "text": "Wat wil je eten?"})]


@pytest.mark.parametrize("changed", ["binding", "owner", "credential"])
def test_changes_after_preparation_prevent_send(monkeypatch, changed):
    provider, turn, state, calls, entry = fixture(monkeypatch)
    request = send_command(provider, turn)
    if changed == "binding":
        state["bindings"] = TelegramPrincipalBindings((replace(entry, principal_id="other"),))
    elif changed == "owner":
        state["allowed"] = False
    else:
        state["token"] = "123:new-token"
    with pytest.raises(ConversationConflict):
        asyncio.run(provider.execute(request))
    assert calls == []


def test_no_durable_acceptance_never_authorizes_retry(monkeypatch):
    provider, turn, state, calls, entry = fixture(monkeypatch)
    request = send_command(provider, turn)
    observation = asyncio.run(provider.observe(request, None))
    assert observation.disposition == "conflict"
    assert calls == []


def test_stop_after_recipient_reads_prevents_send(monkeypatch):
    provider, turn, state, calls, entry = fixture(monkeypatch)
    request = send_command(provider, turn)
    def stop(candidate):
        raise ConversationConflict("source stopped")
    provider.ensure_running = stop
    with pytest.raises(ConversationConflict, match="source stopped"):
        asyncio.run(provider.execute(request))
    assert calls == []


def test_recipient_removed_at_final_resolver_prevents_send(monkeypatch):
    provider, turn, state, calls, entry = fixture(monkeypatch)
    request = send_command(provider, turn)
    original = provider._contacts
    reads = []
    def changing_contacts():
        reads.append(1)
        return {} if len(reads) == 3 else original()
    provider._contacts = changing_contacts
    with pytest.raises(ConversationConflict, match="changed before dispatch"):
        asyncio.run(provider.execute(request))
    assert calls == []


def test_uncertain_network_send_not_retried_by_observation(monkeypatch):
    provider, turn, state, calls, entry = fixture(monkeypatch)
    request = send_command(provider, turn)
    attempted = []
    def uncertain(*args, **kwargs):
        attempted.append(1)
        raise TimeoutError("response lost after possible send")
    monkeypatch.setattr("eva.providers.telegram_contacts.TelegramBotApiAdapter._call", uncertain)
    with pytest.raises(TimeoutError):
        asyncio.run(provider.execute(request))
    assert asyncio.run(provider.observe(request, None)).disposition == "conflict"
    assert len(attempted) == 1


@pytest.mark.parametrize("alteration", ["text", "recipient", "receipt"])
def test_forged_receipt_not_accepted(monkeypatch, alteration):
    provider, turn, state, calls, entry = fixture(monkeypatch)
    request = send_command(provider, turn)
    dispatch = asyncio.run(provider.execute(request))
    result = dict(dispatch.details["result"])
    result[{"text": "text", "recipient": "contact_ref", "receipt": "receipt"}[alteration]] = "other"
    forged = ToolDispatchResult(True, dispatch.provider_ref, {"result": result})
    assert asyncio.run(provider.observe(request, forged)).disposition == "conflict"


def test_provider_rejects_wrong_transport_receipt(monkeypatch):
    provider, turn, state, calls, entry = fixture(monkeypatch)
    request = send_command(provider, turn)
    monkeypatch.setattr("eva.providers.telegram_contacts.TelegramBotApiAdapter._call",
        lambda *args, **kwargs: {"message_id": 77, "chat": {"id": 999, "type": "private"},
                                "text": "Wat wil je eten?"})
    with pytest.raises(RuntimeError, match="receipt"):
        asyncio.run(provider.execute(request))


def test_source_quote_must_exist_in_real_owner_instruction(monkeypatch):
    provider, turn, state, calls, entry = fixture(monkeypatch)
    with pytest.raises(Exception, match="exacte toegelaten"):
        command(provider, turn, SEND, {"contact_ref": next(iter(provider._contacts())),
            "text": "Niet gevraagd", "request_quote": "Invented authorization"})
    assert calls == []


def test_factory_disabled_by_default(monkeypatch):
    monkeypatch.delenv("EVA_TELEGRAM_CONTACTS_ENABLED", raising=False)
    assert TelegramContactsProviderFactory.load_settings() is None
    monkeypatch.setenv("EVA_TELEGRAM_CONTACTS_ENABLED", "true")
    monkeypatch.delenv("EVA_TELEGRAM_BOT_TOKEN_FILE", raising=False)
    with pytest.raises(RuntimeError, match="require existing"):
        TelegramContactsProviderFactory.load_settings()


@pytest.mark.parametrize("uncertain", [False, True])
def test_existing_durable_orchestrator_replay_does_not_send_twice(monkeypatch, uncertain):
    from eva.application.orchestration import VerifiedToolOrchestrator
    from tests.application.test_orchestration import Timeline

    provider, turn, state, calls, entry = fixture(monkeypatch)
    request = send_command(provider, turn)
    timeline = Timeline()
    timeline.state = replace(timeline.state, thread_id=request.thread_id)
    if uncertain:
        def response_lost(api, method, payload, **kwargs):
            calls.append((method, payload))
            raise TimeoutError("acceptance response lost")
        monkeypatch.setattr("eva.providers.telegram_contacts.TelegramBotApiAdapter._call", response_lost)
    first = asyncio.run(VerifiedToolOrchestrator(timeline, provider).run(request))
    resumed = asyncio.run(VerifiedToolOrchestrator(timeline, provider).run(request))
    assert len(calls) == 1
    if uncertain:
        assert first.status != "verified" and resumed.status != "verified"
    else:
        assert first.status == resumed.status == "verified"
