"""Owner-bound delegated work operations, independent of runtime composition."""
from typing import Protocol

from eva.application.conversation import ConversationTurn


class DelegatedWorkOwnerPort(Protocol):
    def available_for(self, turn: ConversationTurn) -> bool: ...

    def validate_authority(self, turn: ConversationTurn) -> None: ...

    def execute(self, operation: str, turn: ConversationTurn, *,
                task: str | None = None, work_id: str | None = None,
                request_key: str) -> dict: ...
