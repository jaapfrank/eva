"""Explicit private completion destinations, separate from immutable work specs."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from uuid import UUID
from datetime import datetime

from eva.application.conversation import ConversationTurn
from eva.application.telegram_ingress import TelegramPrincipalBindings
from eva.contracts.canonical_json import canonical_sha256


@dataclass(frozen=True, slots=True)
class PrivateWorkDestination:
    owner_principal_id: UUID
    principal_binding_id: UUID
    telegram_user_id: int
    chat_id: str
    origin_tenant_id: str
    origin_installation_id: str
    origin_principal_id: str
    origin_conversation_id: str
    origin_message_id: str
    channel: str = "telegram_private"

    def __post_init__(self):
        if (not isinstance(self.owner_principal_id, UUID)
                or not isinstance(self.principal_binding_id, UUID)
                or type(self.telegram_user_id) is not int or self.telegram_user_id < 1
                or not isinstance(self.chat_id, str)
                or not re.fullmatch(r"[1-9][0-9]{0,15}", self.chat_id)
                or self.chat_id != str(self.telegram_user_id)
                or self.channel != "telegram_private"):
            raise ValueError("private work destination is invalid")
        for value in (self.origin_tenant_id, self.origin_installation_id,
                      self.origin_principal_id, self.origin_conversation_id, self.origin_message_id):
            if not isinstance(value, str) or not value.strip() or len(value) > 512:
                raise ValueError("private work origin is invalid")
        if (self.origin_conversation_id != "telegram:" + self.chat_id
                or self.origin_principal_id not in {
                    str(self.owner_principal_id), "telegram:" + self.chat_id}):
            raise ValueError("private work destination differs from its origin")

    @property
    def telegram_message_id(self) -> int:
        """Resolve only the recorded message within its original private chat."""
        prefix = self.origin_conversation_id + ":"
        suffix = self.origin_message_id.removeprefix(prefix)
        if (not self.origin_message_id.startswith(prefix)
                or not re.fullmatch(r"[1-9][0-9]{0,15}", suffix)):
            raise ValueError("private work origin has no Telegram message identity")
        return int(suffix)

    def validate_current(self, bindings: TelegramPrincipalBindings) -> None:
        """A recorded destination never substitutes for a current binding."""
        if (bindings.resolve(self.telegram_user_id, self.chat_id) != str(self.owner_principal_id)
                or bindings.resolve_binding_id(self.telegram_user_id, self.chat_id)
                != self.principal_binding_id):
            raise ValueError("private work destination binding is not current")
        if self.origin_principal_id != str(self.owner_principal_id):
            if bindings.private_bridge_principal(principal_id=str(self.owner_principal_id),
                    principal_binding_id=self.principal_binding_id) != self.origin_principal_id:
                raise ValueError("private work origin alias is not current")

    @classmethod
    def from_telegram_turn(cls, turn: ConversationTurn, *, owner_principal_id: UUID,
                           principal_binding_id: UUID, telegram_user_id: int, chat_id: str,
                           bindings: TelegramPrincipalBindings) -> PrivateWorkDestination:
        if (turn.audience.kind != "private_person"
                or turn.audience.principal_id != turn.principal_id):
            raise ValueError("private work destination requires a private person")
        value = cls(owner_principal_id, principal_binding_id, telegram_user_id, chat_id,
                    turn.tenant_id, turn.installation_id, turn.principal_id,
                    turn.conversation_id, turn.message_id)
        value.validate_current(bindings)
        return value

    def to_payload(self) -> dict:
        return {**asdict(self), "contract": "EvaPrivateWorkDestination/v1",
                "owner_principal_id": str(self.owner_principal_id),
                "principal_binding_id": str(self.principal_binding_id)}

    @classmethod
    def from_payload(cls, raw: object) -> PrivateWorkDestination:
        if not isinstance(raw, dict) or raw.get("contract") != "EvaPrivateWorkDestination/v1":
            raise ValueError("private work destination payload is invalid")
        try:
            fields = {key: value for key, value in raw.items() if key != "contract"}
            fields["owner_principal_id"] = UUID(fields["owner_principal_id"])
            fields["principal_binding_id"] = UUID(fields["principal_binding_id"])
            value = cls(**fields)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise ValueError("private work destination payload is invalid") from error
        if value.to_payload() != raw:
            raise ValueError("private work destination shape differs")
        return value


@dataclass(frozen=True, slots=True)
class AuthorizedWorkDelivery:
    """One current read; callers must reauthorize immediately before dispatch."""
    destination: PrivateWorkDestination
    work_id: str
    spec_sha256: str
    result_sha256: str
    output: str
    outcome: str = "completed"

    @property
    def operation_id(self) -> str:
        return "delegated-completion:" + canonical_sha256({
            "work_id": self.work_id, "spec_sha256": self.spec_sha256,
            "result_sha256": self.result_sha256, "destination": self.destination.to_payload(),
        })


@dataclass(frozen=True, slots=True)
class PrivateWorkDeliveryClaim:
    work_id: str
    owner_principal_id: UUID
    principal_binding_id: UUID
    claim_id: str
    operation_id: str
    deadline: datetime
    revision: int = 1

    def to_payload(self) -> dict:
        return {"work_id": self.work_id, "owner_principal_id": str(self.owner_principal_id),
                "principal_binding_id": str(self.principal_binding_id), "claim_id": self.claim_id,
                "operation_id": self.operation_id, "deadline": self.deadline.isoformat(),
                "revision": self.revision}
