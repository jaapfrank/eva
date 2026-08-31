"""Optional AgentMail provider plug-in behind Eva's generic tool boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
import os

import sqlalchemy as sa

from eva.adapters.connectors.agentmail import AgentMailConfig, AgentMailReadAdapter
from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.adapters.postgres.external_conversation_tools import (
    PostgresExternalConversationTools,
)
from eva.providers.contracts import ProviderContribution


def _value(name: str) -> str:
    return os.environ.get(name, "").strip()


@dataclass(frozen=True, slots=True)
class AgentMailProviderSettings:
    inbox_id: str
    credential: str = field(repr=False)


class AgentMailProviderFactory:
    provider_id = "agentmail"

    @staticmethod
    def load_settings() -> AgentMailProviderSettings | None:
        if "EVA_AGENTMAIL_API_KEY" in os.environ:
            raise RuntimeError(
                "EVA_AGENTMAIL_API_KEY is forbidden; use "
                "EVA_AGENTMAIL_API_KEY_FILE"
            )
        inbox_id = _value("EVA_AGENTMAIL_INBOX_ID")
        credential_file = _value("EVA_AGENTMAIL_API_KEY_FILE")
        if bool(inbox_id) != bool(credential_file):
            raise RuntimeError("Eva external e-mail provider configuration is incomplete")
        if not inbox_id:
            return None
        credential = read_runtime_secret_file(
            credential_file,
            setting_name="EVA_AGENTMAIL_API_KEY_FILE",
        )
        return AgentMailProviderSettings(inbox_id=inbox_id, credential=credential)

    @staticmethod
    def build(
        settings: AgentMailProviderSettings, engine: sa.Engine
    ) -> ProviderContribution:
        adapter = AgentMailReadAdapter(
            AgentMailConfig(
                inbox_id=settings.inbox_id,
                api_key=settings.credential,
            )
        )
        return ProviderContribution(
            provider_id=AgentMailProviderFactory.provider_id,
            conversation_tools=adapter.conversation_tools(),
            external_tools=(
                PostgresExternalConversationTools(
                    engine,
                    adapter.conversation_read_planner(),
                    adapter,
                ),
            ),
        )


__all__ = ["AgentMailProviderFactory", "AgentMailProviderSettings"]
