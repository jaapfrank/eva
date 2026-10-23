"""Explicit owner-requested contact through the existing Telegram transport."""
from __future__ import annotations

from dataclasses import dataclass
import asyncio
import os

from eva.adapters.connectors.telegram import TelegramBotApiAdapter, TelegramProactiveDelivery
from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.adapters.credentials.telegram_bindings import load_principal_bindings
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.application.conversation import (
    ConversationActionInputRejected, ConversationConflict, ConversationToolDescriptor,
    ConversationToolPreparation,
)
from eva.application.orchestration import ToolDispatchResult, ToolProofObservation
from eva.application.background_execution import authorize_background_dispatch
from eva.application.cancellation import check_cancellation
from eva.contracts.canonical_json import canonical_sha256
from eva.providers.contracts import ProviderContribution
from eva.providers.mcp_autonomy import _SourceTurnLoader
from eva.providers.private_owner_authority import configured_private_owner_resolver

LIST = "list_telegram_contacts"
SEND = "send_telegram_message"


def _source(turn):
    return {**{key: getattr(turn, key) for key in (
        "tenant_id", "installation_id", "principal_id", "conversation_id", "message_id")},
        "audience": {"kind": turn.audience.kind, "principal_id": turn.audience.principal_id}}


class TelegramContactAuthority:
    def __init__(self, resolver, *, tenant_id, installation_id, owner_id):
        self.resolver, self.owner_id = resolver, owner_id
        self.scope = external_timeline_scope(tenant_id, installation_id, "Telegram owner scope required")

    def authorize_turn(self, turn):
        if (self.resolver is None or self.resolver(turn) != self.owner_id
                or turn.observation_binding is not None
                or turn.conversation_id.startswith("initiative-task:")):
            raise ConversationConflict("Telegram contact requires the current private owner instruction")


class TelegramContacts:
    action_names = (LIST, SEND)

    def __init__(self, *, authority, load_source, bindings, credential, configuration_sha256,
                 ensure_running):
        self.authority, self.load_source = authority, load_source
        self.bindings, self.credential = bindings, credential
        self.ensure_running = ensure_running
        self.configuration_sha256 = configuration_sha256
        self._credential_sha256 = canonical_sha256(credential())

    def authorize_turn(self, turn):
        self.authority.authorize_turn(turn)
        if canonical_sha256(self.credential()) != self._credential_sha256:
            raise ConversationConflict("Telegram transport credential changed")

    @staticmethod
    def conversation_tools():
        guidance = ("Gebruik deze directe Telegram-route voor een expliciet gevraagde vraag of bericht aan "
            "een toegelaten gezinscontact; zoek daarvoor niet in Home Assistant. Deel alleen wat de eigenaar "
            "vroeg te delen. Een profielnaam is een label, geen identiteitsbewijs. Bij dubbelzinnige namen vraag "
            "wie bedoeld wordt. Gebruik nooit /link om een ander gezinslid toe te voegen. Een Telegram-receipt "
            "bewijst verzending aan het contact, niet dat het bericht gelezen of beantwoord is. "
            "Bij onzekere verzending nooit opnieuw sturen met een nieuwe toolaanroep.")
        return (
            ConversationToolDescriptor(LIST, "Lees toegelaten privécontacten en hun huidige Telegram-profielnaam.",
                {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, guidance),
            ConversationToolDescriptor(SEND, "Stuur één bericht aan een exact toegelaten Telegram-contact, uitsluitend "
                "op uitdrukkelijk verzoek van de eigenaar. Gebruik de contact_ref uit list_telegram_contacts.",
                {"type": "object", "properties": {
                    "contact_ref": {"type": "string"},
                    "text": {"type": "string", "minLength": 1, "maxLength": 4096},
                    "request_quote": {"type": "string", "minLength": 1,
                        "description": "Exact citaat uit de huidige eigenaaropdracht dat het versturen vraagt."}},
                 "required": ["contact_ref", "text", "request_quote"], "additionalProperties": False}, guidance),
        )

    @staticmethod
    def contact_reply_allowed(turn, receipt):
        return receipt.call.name == SEND and receipt.output.get("status") == "verified"

    def _contacts(self):
        # The existing loader validates this trusted host file. Names never grant access.
        bindings = self.bindings()
        contacts = {}
        for entry in bindings._by_user.values():
            chat = str(entry.telegram_user_id)
            if chat not in entry.allowed_chat_ids or entry.principal_binding_id is None:
                continue
            identity = {"chat_id": chat, "principal_id": entry.principal_id,
                        "principal_binding_id": entry.principal_binding_id}
            contacts["telegram-contact:" + canonical_sha256(identity)] = identity
        if len(contacts) > 32:
            raise ConversationConflict("Telegram private contact list exceeds its bound")
        return contacts

    def _validate(self, turn, name, arguments):
        if name == LIST and arguments == {}:
            return
        if (name != SEND or set(arguments) != {"contact_ref", "text", "request_quote"}
                or any(not isinstance(arguments[key], str) or not arguments[key].strip() for key in arguments)
                or len(arguments["text"]) > 4096
                or arguments["request_quote"] not in turn.text):
            raise ConversationActionInputRejected(("contact_ref", "text", "request_quote"),
                prompt="Gebruik het exacte toegelaten contact en een citaat uit de echte huidige opdracht om te sturen.")
        if arguments["contact_ref"] not in self._contacts():
            raise ConversationConflict("Telegram recipient binding is absent or changed")

    async def prepare(self, turn, call):
        self.authorize_turn(turn)
        self._validate(turn, call.name, call.arguments)
        arguments = {"source": _source(turn), "request": call.arguments}
        proof = {"request_sha256": canonical_sha256(arguments), "tool": call.name}
        return ConversationToolPreparation(tool_name=call.name, arguments=arguments,
            expected_proof=proof, receipt_output={}, observation_tool_name=call.name,
            observation_arguments=arguments, observation_output_key="result",
            tool_configuration_sha256=self.configuration_sha256, execution_timeout_seconds=20)

    def _bound(self, command):
        if (command.tool_name not in self.action_names
                or command.tool_configuration_sha256 != self.configuration_sha256
                or set(command.arguments) != {"source", "request"}
                or command.observation_tool_name != command.tool_name
                or command.observation_arguments != command.arguments
                or command.expected_proof != {"request_sha256": canonical_sha256(command.arguments),
                                               "tool": command.tool_name}):
            raise ConversationConflict("Telegram contact request binding differs")
        turn = self.load_source(command.arguments["source"])
        self.authorize_turn(turn)
        if _source(turn) != command.arguments["source"]:
            raise ConversationConflict("Telegram contact source differs")
        self._validate(turn, command.tool_name, command.arguments["request"])
        return turn

    def _execute(self, command):
        turn = self._bound(command)
        if command.tool_name == LIST:
            contacts = self._contacts()
            api = TelegramBotApiAdapter(bot_token=self.credential(), timeout_seconds=2)
            rows = []
            for ref, identity in contacts.items():
                profile = api._call("getChat", {"chat_id": identity["chat_id"]}, maximum_response_bytes=8192)
                if (not isinstance(profile, dict) or type(profile.get("id")) is not int
                        or str(profile["id"]) != identity["chat_id"] or profile.get("type") != "private"):
                    raise ConversationConflict("Telegram contact profile belongs to another recipient")
                rows.append({"contact_ref": ref, **{key: profile.get(key) for key in
                    ("first_name", "last_name", "username")}, "identity_source": "trusted_private_binding"})
            self.authorize_turn(turn)
            if contacts != self._contacts():
                raise ConversationConflict("Telegram contacts changed during discovery")
            result = {"contacts": rows, "conversation_content_read": False}
            return ToolDispatchResult(True, None, {"result": result})
        arguments = command.arguments["request"]
        ref = arguments["contact_ref"]
        identity = self._contacts()[ref]
        def current_recipient():
            self.authorize_turn(turn)
            current = self._contacts().get(ref)
            if current != identity:
                raise ConversationConflict("Telegram recipient changed before dispatch")
            self.ensure_running(turn)
            authorize_background_dispatch(turn)
            check_cancellation()
            return current["chat_id"]
        delivery = TelegramProactiveDelivery(bot_token=self.credential(),
            recipient_resolver=current_recipient, timeout_seconds=10)
        receipt = delivery.deliver_text(arguments["text"], expected_chat_id=identity["chat_id"])
        result = {"contact_ref": ref, "text": arguments["text"], "receipt": receipt,
                  "delivery": "telegram_accepted", "read_by_recipient": False}
        return ToolDispatchResult(True, receipt, {"result": result})

    async def execute(self, command):
        return await asyncio.to_thread(self._execute, command)

    async def observe(self, command, dispatch):
        await asyncio.to_thread(self._bound, command)
        # Telegram has no idempotency key or general sent-message readback API.
        # Missing durable acceptance must never authorize a second send.
        if dispatch is None:
            return ToolProofObservation("conflict", {}, {"reason": "telegram_acceptance_not_recorded"})
        result = dispatch.details.get("result")
        if not dispatch.accepted or not isinstance(result, dict):
            return ToolProofObservation("conflict", {}, {})
        if command.tool_name == SEND:
            arguments = command.arguments["request"]
            identity = self._contacts()[arguments["contact_ref"]]
            prefix = "telegram:" + identity["chat_id"] + ":"
            receipt = result.get("receipt")
            if (result.get("contact_ref") != arguments["contact_ref"]
                    or result.get("text") != arguments["text"]
                    or not isinstance(receipt, str) or not receipt.startswith(prefix)
                    or not receipt[len(prefix):].isdigit() or int(receipt[len(prefix):]) < 1
                    or dispatch.provider_ref != receipt or result.get("delivery") != "telegram_accepted"):
                return ToolProofObservation("conflict", {}, {})
        return ToolProofObservation("verified", command.expected_proof,
            {"source": "telegram_bot_api", "observation": result,
             "observation_sha256": canonical_sha256(result)})


@dataclass(frozen=True, slots=True)
class TelegramContactSettings:
    token_file: str
    bindings_file: str
    tenant_id: str
    installation_id: str
    owner_id: str


class TelegramContactsProviderFactory:
    provider_id = "telegram_contacts"
    supports_digital_work = True
    execution_sources = ("providers/telegram_contacts.py", "adapters/connectors/telegram.py",
        "adapters/credentials/telegram_bindings.py", "providers/private_owner_authority.py",
        "adapters/postgres/private_owner.py", "providers/mcp_autonomy.py")

    @staticmethod
    def load_settings():
        enabled = os.environ.get("EVA_TELEGRAM_CONTACTS_ENABLED", "").strip().lower()
        if enabled in {"", "false"}:
            return None
        if enabled != "true":
            raise RuntimeError("Telegram contacts enable setting is invalid")
        values = [os.environ.get(name, "").strip() for name in (
            "EVA_TELEGRAM_BOT_TOKEN_FILE", "EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE",
            "EVA_RUNTIME_TENANT_ID", "EVA_RUNTIME_INSTALLATION_ID", "EVA_RUNTIME_PRINCIPAL_ID")]
        if not all(values):
            raise RuntimeError("Telegram contacts require existing token, bindings and private owner")
        return TelegramContactSettings(*values)

    @staticmethod
    def build(settings, engine):
        resolver = configured_private_owner_resolver(engine, tenant_id=settings.tenant_id,
                                                     installation_id=settings.installation_id)
        if resolver is None:
            raise RuntimeError("Telegram contacts require current private owner authority")
        authority = TelegramContactAuthority(resolver, tenant_id=settings.tenant_id,
            installation_id=settings.installation_id, owner_id=settings.owner_id)
        credential = lambda: read_runtime_secret_file(settings.token_file, setting_name="EVA_TELEGRAM_BOT_TOKEN_FILE")
        adapter = TelegramContacts(authority=authority, load_source=_SourceTurnLoader(engine, authority),
            bindings=lambda: load_principal_bindings(settings.bindings_file), credential=credential,
            ensure_running=PostgresConversationJournal(engine, authority.scope).ensure_running,
            configuration_sha256=canonical_sha256({"contract": "EvaTelegramContacts/v1",
                "tenant": settings.tenant_id, "installation": settings.installation_id,
                "owner": settings.owner_id, "credential_sha256": canonical_sha256(credential()),
                "bindings_file": settings.bindings_file}))
        return ProviderContribution(provider_id="telegram_contacts", conversation_tools=adapter.conversation_tools(),
            external_tools=(PostgresExternalConversationTools(engine, adapter, adapter,
                authorize_turn=adapter.authorize_turn, configuration_sha256=adapter.configuration_sha256),),
            digital_action_names=adapter.action_names)
