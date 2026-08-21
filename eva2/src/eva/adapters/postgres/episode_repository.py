"""Connection-injected SQLAlchemy Core repository for tranche 4B."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import Connection, RootTransaction, RowMapping

from eva.adapters.postgres.schema import episode_events, episodes
from eva.adapters.postgres.scope import set_local_scope
from eva.application.episode_repository import (
    AppliedEpisodeTransition,
    ConcurrentEpisodeWrite,
    CorruptEpisodeState,
    EpisodeCommandConflict,
    EpisodeNotFound,
    TransactionRequired,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.lifecycle import EpisodeState
from eva.domain.episode import (
    EpisodeAggregate,
    EpisodeTransition,
    EpisodeTransitionCommand,
    episode_transition_command_sha256,
    transition_accepted_to_planning,
)


def _as_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise CorruptEpisodeState("database timestamp is not timezone-aware")
    utc = value.astimezone(timezone.utc)
    if utc.microsecond:
        raise CorruptEpisodeState("database timestamp is not canonical UTC seconds")
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _uuid(value: str) -> UUID:
    return UUID(value)


def _hash_bytes(value: str) -> bytes:
    return bytes.fromhex(value)


class PostgresEpisodeRepository:
    """Repository that never owns the transaction or the database engine."""

    def __init__(
        self, connection: Connection, tenant_id: str, installation_id: str
    ) -> None:
        transaction = connection.get_transaction()
        if (
            transaction is None
            or not transaction.is_active
            or connection.in_nested_transaction()
        ):
            raise TransactionRequired()
        self._connection = connection
        self._transaction: RootTransaction = transaction
        self._tenant_id = tenant_id
        self._installation_id = installation_id

    def _require_original_transaction(self) -> None:
        current = self._connection.get_transaction()
        if (
            current is not self._transaction
            or not self._transaction.is_active
            or self._connection.in_nested_transaction()
        ):
            raise TransactionRequired()

    def _set_scope(self) -> None:
        set_local_scope(
            self._connection,
            tenant_id=self._tenant_id,
            installation_id=self._installation_id,
        )

    def _load(self, episode_id: str) -> EpisodeAggregate:
        row = (
            self._connection.execute(
                sa.select(
                    episodes.c.installation_id,
                    episodes.c.episode_id,
                    episodes.c.tenant_id,
                    episodes.c.principal_id,
                    episodes.c.conversation_id,
                    episodes.c.ingress_request_id,
                    episodes.c.state,
                    episodes.c.current_generation,
                    episodes.c.supersedes_generation,
                    episodes.c.revision,
                    episodes.c.last_event_id,
                    episodes.c.state_contract,
                    episodes.c.state_schema_version,
                    episodes.c.state_payload_sha256,
                    episodes.c.updated_at,
                ).where(
                    episodes.c.tenant_id == _uuid(self._tenant_id),
                    episodes.c.installation_id == _uuid(self._installation_id),
                    episodes.c.episode_id == _uuid(episode_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise EpisodeNotFound()
        if (
            row["state_contract"] != "EpisodeState/v1"
            or row["state_schema_version"] != 1
        ):
            raise CorruptEpisodeState("unknown stored episode contract")
        state = EpisodeState.model_validate(
            {
                "schema_version": 1,
                "contract": "EpisodeState/v1",
                "episode_id": str(row["episode_id"]),
                "tenant_id": str(row["tenant_id"]),
                "principal_id": str(row["principal_id"]),
                "conversation_id": str(row["conversation_id"]),
                "ingress_request_id": str(row["ingress_request_id"]),
                "state": row["state"],
                "current_generation": row["current_generation"],
                "supersedes_generation": row["supersedes_generation"],
                "last_event_id": str(row["last_event_id"]),
                "updated_at": _as_timestamp(row["updated_at"]),
            }
        )
        try:
            stored_hash = bytes(row["state_payload_sha256"]).hex()
        except (TypeError, ValueError) as exc:
            raise CorruptEpisodeState("invalid stored episode hash") from exc
        try:
            return EpisodeAggregate(
                installation_id=str(row["installation_id"]),
                revision=row["revision"],
                state=state,
                state_sha256=stored_hash,
            )
        except ValueError as exc:
            raise CorruptEpisodeState(str(exc)) from exc

    def _existing_command(
        self, command: EpisodeTransitionCommand, expected_command_sha256: str
    ) -> AppliedEpisodeTransition | None:
        row = (
            self._connection.execute(
                sa.select(
                    episode_events.c.command_sha256,
                    episode_events.c.after_revision,
                    episode_events.c.after_episode_state,
                    episode_events.c.after_state_sha256,
                ).where(
                    episode_events.c.tenant_id == _uuid(self._tenant_id),
                    episode_events.c.installation_id == _uuid(self._installation_id),
                    episode_events.c.command_id == _uuid(command.command_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        if bytes(row["command_sha256"]).hex() != expected_command_sha256:
            raise EpisodeCommandConflict()
        return self._applied_from_event(row)

    @staticmethod
    def _applied_from_event(row: RowMapping) -> AppliedEpisodeTransition:
        try:
            state = EpisodeState.model_validate(row["after_episode_state"])
        except ValueError as exc:
            raise CorruptEpisodeState("invalid stored event snapshot") from exc
        if canonical_sha256(state) != bytes(row["after_state_sha256"]).hex():
            raise CorruptEpisodeState("stored event snapshot hash does not match")
        return AppliedEpisodeTransition(state=state, revision=row["after_revision"])

    def _update_head(self, transition: EpisodeTransition) -> bool:
        result = self._connection.execute(
            sa.update(episodes)
            .where(
                episodes.c.tenant_id == _uuid(self._tenant_id),
                episodes.c.installation_id == _uuid(self._installation_id),
                episodes.c.episode_id == _uuid(transition.before.state.episode_id),
                episodes.c.revision == transition.before.revision,
                episodes.c.state == transition.before.state.state,
                episodes.c.current_generation
                == transition.before.state.current_generation,
                episodes.c.last_event_id
                == _uuid(transition.before.state.last_event_id),
                episodes.c.state_payload_sha256
                == _hash_bytes(transition.before.state_sha256),
            )
            .values(
                state=transition.after.state.state,
                current_generation=transition.after.state.current_generation,
                supersedes_generation=transition.after.state.supersedes_generation,
                revision=transition.after.revision,
                last_event_id=_uuid(transition.after.state.last_event_id),
                state_payload_sha256=_hash_bytes(transition.after.state_sha256),
                updated_at=_to_datetime(transition.after.state.updated_at),
            )
        )
        return result.rowcount == 1

    def _insert_event(self, transition: EpisodeTransition) -> None:
        result = self._connection.execute(
            postgres_insert(episode_events)
            .values(
                tenant_id=_uuid(self._tenant_id),
                installation_id=_uuid(self._installation_id),
                episode_id=_uuid(transition.after.state.episode_id),
                event_id=_uuid(transition.event_id),
                command_id=_uuid(transition.command_id),
                command_sha256=_hash_bytes(transition.command_sha256),
                event_kind="state_transition",
                before_revision=transition.before.revision,
                after_revision=transition.after.revision,
                before_state=transition.before.state.state,
                after_state=transition.after.state.state,
                before_generation=transition.before.state.current_generation,
                after_generation=transition.after.state.current_generation,
                before_episode_state=transition.before.state.model_dump(mode="json"),
                after_episode_state=transition.after.state.model_dump(mode="json"),
                before_state_sha256=_hash_bytes(transition.before.state_sha256),
                after_state_sha256=_hash_bytes(transition.after.state_sha256),
                occurred_at=_to_datetime(transition.after.state.updated_at),
            )
            .on_conflict_do_nothing(constraint="uq_episode_events_scoped_command")
            .returning(episode_events.c.event_id)
        )
        if result.scalar_one_or_none() is None:
            raise EpisodeCommandConflict()

    def load_state(self, episode_id: str) -> EpisodeAggregate:
        """Read canonical state through the same explicit tenant boundary."""

        self._require_original_transaction()
        self._set_scope()
        return self._load(episode_id)

    def apply_transition(
        self, command: EpisodeTransitionCommand
    ) -> AppliedEpisodeTransition:
        self._require_original_transaction()
        if (
            command.tenant_id != self._tenant_id
            or command.installation_id != self._installation_id
        ):
            raise EpisodeCommandConflict()
        self._set_scope()
        command_sha256 = episode_transition_command_sha256(command)
        replay = self._existing_command(command, command_sha256)
        if replay is not None:
            return replay

        aggregate = self._load(command.episode_id)
        transition = transition_accepted_to_planning(aggregate, command)
        if not self._update_head(transition):
            replay = self._existing_command(command, command_sha256)
            if replay is not None:
                return replay
            raise ConcurrentEpisodeWrite()
        self._insert_event(transition)
        return AppliedEpisodeTransition(
            state=transition.after.state,
            revision=transition.after.revision,
        )
