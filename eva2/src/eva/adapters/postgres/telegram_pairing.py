"""Durable two-sided Telegram account pairing on Eva's operational timeline."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.engine import Engine

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    CheckpointState,
    PostgresOperationalTimeline,
    TimelineScope,
)
from eva.application.telegram_pairing import (
    TelegramDynamicBinding,
    TelegramPairingChallenge,
)
from eva.application.telegram_ingress import TelegramPrincipalBindings
from eva.contracts.base import JsonValue


class PostgresTelegramPairingStore:
    """Only a private-DM code confirmed by another known account creates a binding."""

    def __init__(
        self,
        engine: Engine,
        *,
        tenant_id: str,
        installation_id: str,
        trusted_bindings: TelegramPrincipalBindings,
    ) -> None:
        if not tenant_id.strip() or not installation_id.strip():
            raise ValueError("Telegram pairing store scope is required")
        self._timeline = PostgresOperationalTimeline(
            engine,
            TimelineScope(
                tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
                installation_id=uuid5(
                    NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"
                ),
            ),
        )
        self._trusted_bindings = trusted_bindings

    @staticmethod
    def _request_thread(update_id: int) -> str:
        if update_id < 0:
            raise ValueError("Telegram pairing update is invalid")
        return f"telegram-pairing-request:{update_id}"

    @staticmethod
    def _binding_thread(telegram_user_id: int) -> str:
        if telegram_user_id < 1:
            raise ValueError("Telegram pairing user is invalid")
        return f"telegram-dynamic-binding:{telegram_user_id}"

    @staticmethod
    def _challenge_data(
        challenge: TelegramPairingChallenge, *, update_id: int
    ) -> dict[str, JsonValue]:
        return {
            "code_sha256": challenge.code_sha256,
            "expires_at": challenge.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "requested_chat_id": challenge.requested_chat_id,
            "requested_user_id": challenge.requested_user_id,
            "update_id": update_id,
        }

    def start(
        self, challenge: TelegramPairingChallenge, *, update_id: int, now: datetime
    ) -> TelegramPairingChallenge:
        thread_id = self._request_thread(update_id)
        data = self._challenge_data(challenge, update_id=update_id)
        try:
            created = self._timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"{thread_id}:requested",
                operation_id=f"telegram-pairing-request:{update_id}",
                occurred_at=now,
                data=data,
                event_type="TELEGRAM_PAIRING_REQUESTED",
                phase="pairing_requested",
                status="active",
            )
            self._await_confirmation(
                thread_id=thread_id,
                state=created,
                challenge=challenge,
                update_id=update_id,
                now=now,
            )
            return challenge
        except CheckpointConflict:
            events = self._timeline.events(thread_id)
            if not events or events[0].event_type != "TELEGRAM_PAIRING_REQUESTED":
                raise RuntimeError("Telegram pairing request identity collision") from None
            stored = events[0].payload.get("data")
            if not isinstance(stored, dict):
                raise RuntimeError("Telegram pairing request payload is invalid")
            if not self._matches_request(stored, challenge=challenge, update_id=update_id):
                raise RuntimeError("Telegram pairing request identity collision")
            state = self._timeline.load(thread_id)
            rehydrated = self._challenge_from_data(stored, code=challenge.code)
            self._await_confirmation(
                thread_id=thread_id,
                state=state,
                challenge=rehydrated,
                update_id=update_id,
                now=now,
            )
            return rehydrated

    def confirm(
        self,
        code: str,
        *,
        confirmer_user_id: int,
        confirmer_chat_id: str,
        now: datetime,
    ) -> TelegramDynamicBinding | None:
        principal_id = self._trusted_bindings.resolve(
            confirmer_user_id, confirmer_chat_id
        )
        if principal_id is None:
            return None
        challenge_thread = self._find_challenge_thread(code)
        if challenge_thread is None:
            return None
        state = self._timeline.load(challenge_thread)
        events = self._timeline.events(challenge_thread)
        if not events:
            raise RuntimeError("Telegram pairing challenge disappeared")
        data = events[0].payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Telegram pairing challenge payload is invalid")
        challenge = self._challenge_from_data(data, code=code)
        if state.status == "completed":
            return self._binding_for(challenge.requested_user_id)
        if state.status != "waiting_for_proof" or not challenge.may_confirm(
            user_id=confirmer_user_id, chat_id=confirmer_chat_id, now=now
        ):
            return None
        binding = TelegramDynamicBinding(challenge.requested_user_id, principal_id)
        self._create_binding(binding, now=now)
        try:
            self._timeline.transition(
                thread_id=challenge_thread,
                expected_revision=state.revision,
                checkpoint_id=f"{challenge_thread}:confirmed",
                operation_id=state.operation_id,
                occurred_at=now,
                data={
                    "confirmer_user_id": confirmer_user_id,
                    "principal_id": principal_id,
                    "requested_user_id": challenge.requested_user_id,
                },
                event_type="TELEGRAM_PAIRING_CONFIRMED",
                phase="pairing_confirmed",
                status="completed",
            )
        except CheckpointConflict:
            current = self._timeline.load(challenge_thread)
            if current.status != "completed":
                raise
        return binding

    def resolve(self, *, telegram_user_id: int, chat_id: str) -> str | None:
        if telegram_user_id < 1 or chat_id != str(telegram_user_id):
            return None
        binding = self._binding_for(telegram_user_id)
        return None if binding is None else binding.principal_id

    def _find_challenge_thread(self, code: str) -> str | None:
        code_sha256 = TelegramPairingChallenge(
            code=code,
            requested_user_id=1,
            requested_chat_id="1",
            expires_at=datetime(2000, 1, 1, tzinfo=UTC),
        ).code_sha256
        for state in self._timeline.list_checkpoints(
            thread_prefix="telegram-pairing-request:", limit=256
        ):
            events = self._timeline.events(state.thread_id)
            if not events:
                continue
            data = events[0].payload.get("data")
            if isinstance(data, dict) and data.get("code_sha256") == code_sha256:
                return state.thread_id
        return None

    def _create_binding(self, binding: TelegramDynamicBinding, *, now: datetime) -> None:
        thread_id = self._binding_thread(binding.telegram_user_id)
        data: dict[str, JsonValue] = {
            "principal_id": binding.principal_id,
            "telegram_user_id": binding.telegram_user_id,
        }
        try:
            self._timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"{thread_id}:bound",
                operation_id=f"telegram-dynamic-binding:{binding.telegram_user_id}",
                occurred_at=now,
                data=data,
                event_type="TELEGRAM_DYNAMIC_BINDING_CREATED",
                phase="bound",
                status="completed",
            )
        except CheckpointConflict:
            existing = self._binding_for(binding.telegram_user_id)
            if existing != binding:
                raise RuntimeError("Telegram account is already bound to another person") from None

    def _binding_for(self, telegram_user_id: int) -> TelegramDynamicBinding | None:
        events = self._timeline.events(self._binding_thread(telegram_user_id))
        if not events:
            return None
        data = events[0].payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Telegram dynamic binding payload is invalid")
        principal_id = data.get("principal_id")
        user_id = data.get("telegram_user_id")
        if not isinstance(principal_id, str) or user_id != telegram_user_id:
            raise RuntimeError("Telegram dynamic binding data is invalid")
        return TelegramDynamicBinding(telegram_user_id, principal_id)

    @staticmethod
    def _challenge_from_data(
        data: dict[str, JsonValue], *, code: str
    ) -> TelegramPairingChallenge:
        # A code is deliberately absent from the append-only timeline. This
        # placeholder only carries the non-secret data needed to validate the
        # confirmer; code lookup always compares its SHA-256.
        expires_raw = data.get("expires_at")
        user_id = data.get("requested_user_id")
        chat_id = data.get("requested_chat_id")
        if (
            not isinstance(expires_raw, str)
            or not isinstance(user_id, int)
            or not isinstance(chat_id, str)
        ):
            raise RuntimeError("Telegram pairing challenge data is invalid")
        try:
            expires_at = datetime.strptime(expires_raw, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=UTC
            )
        except ValueError as error:
            raise RuntimeError("Telegram pairing challenge expiry is invalid") from error
        return TelegramPairingChallenge(code, user_id, chat_id, expires_at)

    @staticmethod
    def _matches_request(
        data: dict[str, JsonValue], *, challenge: TelegramPairingChallenge, update_id: int
    ) -> bool:
        return (
            data.get("code_sha256") == challenge.code_sha256
            and data.get("requested_chat_id") == challenge.requested_chat_id
            and data.get("requested_user_id") == challenge.requested_user_id
            and data.get("update_id") == update_id
        )

    def _await_confirmation(
        self,
        *,
        thread_id: str,
        state: CheckpointState,
        challenge: TelegramPairingChallenge,
        update_id: int,
        now: datetime,
    ) -> None:
        if state.status == "waiting_for_proof":
            if state.deadline != challenge.expires_at:
                raise RuntimeError("Telegram pairing challenge expiry collision")
            return
        if state.status != "active":
            raise RuntimeError("Telegram pairing request cannot be resumed")
        try:
            self._timeline.transition(
                thread_id=thread_id,
                expected_revision=state.revision,
                checkpoint_id=f"{thread_id}:awaiting-confirmation",
                operation_id=state.operation_id,
                occurred_at=now,
                deadline=challenge.expires_at,
                data={"code_sha256": challenge.code_sha256, "update_id": update_id},
                event_type="TELEGRAM_PAIRING_AWAITING_CONFIRMATION",
                phase="awaiting_confirmation",
                status="waiting_for_proof",
            )
        except CheckpointConflict:
            recovered = self._timeline.load(thread_id)
            if (
                recovered.status != "waiting_for_proof"
                or recovered.deadline != challenge.expires_at
            ):
                raise


__all__ = ["PostgresTelegramPairingStore"]
