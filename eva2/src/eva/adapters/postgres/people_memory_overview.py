"""Bounded, validated PostgreSQL projection for the people/memory cockpit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import re
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa

from sqlalchemy.engine import Engine

from eva.adapters.postgres.memory_outbox import (
    conversation_memory_document_from_outbox_data,
)
from eva.adapters.postgres.operational_timeline import (
    CheckpointState,
    PostgresOperationalTimeline,
    TimelineEvent,
    TimelineScope,
)
from eva.application.people_memory_overview import (
    DynamicAccountState,
    GroupSharingState,
    HindsightState,
    MemoryFlowState,
    PeopleMemoryOverview,
    PeopleMemoryOverviewError,
)
from eva.application.memory import ConversationMemoryDocument, MemoryRetentionReceipt, RecallCurrentFacts
from eva.application.memory_audience import TelegramGroupConsentReference
from eva.application.telegram_group_consent import TelegramGroupConsent
from eva.application.telegram_pairing import TelegramDynamicBinding
from eva.application.telegram_ingress import TelegramPrincipalBindings
from eva.application.people_memory_overview import (
    MemorySource, MemorySourcePage, memory_source_page_request,
)
from eva.adapters.postgres.current_catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    operational_events, operational_checkpoints, external_timeline_scope,
)
from eva.contracts.canonical_json import canonical_sha256


def require_private_owner_binding(
    conn: sa.Connection, *, scope: TimelineScope, owner: UUID, binding: UUID, now: datetime,
):
    """Reuse the existing snapshot-local current owner check without row locks."""
    bindings = metadata.tables["eva_core.principal_bindings"]
    people = metadata.tables["eva_core.principals"]
    authority = conn.execute(sa.select(bindings.c.principal_id, bindings.c.provider_id,
                                       bindings.c.channel_id).join(
        people, sa.and_(people.c.tenant_id == bindings.c.tenant_id,
                        people.c.principal_id == bindings.c.principal_id)
    ).where(
        bindings.c.tenant_id == scope.tenant_id,
        bindings.c.principal_binding_id == binding,
        bindings.c.principal_id == owner,
        bindings.c.status == "verified", people.c.status == "active",
        bindings.c.retention_until > now, people.c.retention_until > now,
        bindings.c.verified_at <= now,
    )).one_or_none()
    if authority is None or authority.principal_id != owner:
        raise PeopleMemoryOverviewError("owner memory binding is not current")
    return authority


class PostgresOwnerMemorySources:
    """A bounded projection of existing sources, never a second memory store."""

    def __init__(self, engine: Engine, *, tenant_id: str, installation_id: str,
                 owner: UUID, binding: UUID, authority_scope: TimelineScope,
                 telegram_bindings: TelegramPrincipalBindings | None = None) -> None:
        self._engine = engine
        self._tenant = tenant_id
        self._installation = installation_id
        self._owner = owner
        self._binding = binding
        self._bindings = telegram_bindings
        self._authority_scope = authority_scope
        self._scope = external_timeline_scope(tenant_id, installation_id, "memory scope required")

    def page(self, *, now: datetime, limit: int = 20,
             before: str | None = None) -> MemorySourcePage:
        return self._read(now=now, limit=limit, before=before)

    def document(self, source_id: str, *, now: datetime):
        """Resolve an exact source through the same current owner and audience checks."""
        if not isinstance(source_id, str) or str(UUID(source_id)) != source_id:
            raise ValueError("memory source identity is invalid")
        found = self._read(now=now, limit=1, source_id=source_id)
        if found is None:
            raise LookupError("owner memory source unavailable")
        return found

    def recent_other_channel(self, turn, *, now, after=None):
        """Read one retained private exchange across the verified owner bridge."""
        alias = None if self._bindings is None else self._bindings.private_bridge_principal(
            principal_id=str(self._owner), principal_binding_id=self._binding)
        owner = str(self._owner)
        dashboard = f"dashboard:{self._binding}"
        private = turn.audience.kind == "private_person"
        admitted = ((turn.principal_id == owner and
                     (turn.conversation_id == dashboard or turn.conversation_id.startswith("voice:")))
                    or (alias is not None and turn.principal_id == alias
                        and turn.conversation_id == alias))
        if (turn.tenant_id != self._tenant or turn.installation_id != self._installation
                or not private or turn.audience.principal_id != turn.principal_id
                or not admitted or turn.project_id is not None):
            return None
        channels = tuple(value for value in (dashboard, alias)
                         if value is not None and value != turn.conversation_id)
        return self._read(now=now, limit=1, context_conversations=channels, after=after,
                          context_voice=True, exclude_conversation=turn.conversation_id)

    def search_private_history(self, turn, query_text: str, *, now: datetime,
                               limit: int = 3) -> tuple[tuple[ConversationMemoryDocument, str], ...]:
        """Select at most three newest lexical matches across admitted private channels.

        Search is applied in PostgreSQL before the result limit, so older retained
        conversations are eligible. This does not rank semantic relevance or claim
        an exhaustive history; current private owner authority is checked in _read.
        """
        owner = str(self._owner)
        alias = None if self._bindings is None else self._bindings.private_bridge_principal(
            principal_id=owner, principal_binding_id=self._binding)
        dashboard = f"dashboard:{self._binding}"
        admitted = ((turn.principal_id == owner and
                     (turn.conversation_id == dashboard or turn.conversation_id.startswith("voice:")))
                    or (alias is not None and turn.principal_id == alias
                        and turn.conversation_id == alias))
        if (turn.tenant_id != self._tenant or turn.installation_id != self._installation
                or turn.audience.kind != "private_person"
                or turn.audience.principal_id != turn.principal_id
                or not admitted or turn.project_id is not None):
            return ()
        if (not isinstance(query_text, str) or not query_text.strip()
                or len(query_text.encode("utf-8")) > 1024
                or type(limit) is not int or not 1 <= limit <= 3):
            raise ValueError("private history query or result limit is invalid")
        channels = tuple(value for value in (dashboard, alias) if value is not None)
        return self._read(now=now, limit=limit, context_conversations=channels,
                          context_voice=True, history_query=query_text)

    def selected_private_sources(
        self, query: RecallCurrentFacts,
        selected_refs: tuple[tuple[str, str, str], ...], *, now: datetime,
    ) -> tuple[dict[str, object], ...]:
        """Resolve at most three exact original ingresses selected by semantic recall.

        The canonical verified owner can have server-admitted project conversations.
        A Telegram bridge remains tied to its exact private chat. This method does
        not search history or infer missing source identities from derived text.
        """
        owner = str(self._owner)
        alias = None if self._bindings is None else self._bindings.private_bridge_principal(
            principal_id=owner, principal_binding_id=self._binding)
        if (query.tenant_id != self._tenant or query.installation_id != self._installation
                or query.audience.kind != "private_person"
                or query.audience.principal_id != query.principal_id
                or not (query.principal_id == owner or (alias is not None
                    and query.principal_id == alias and query.conversation_id == alias))
                or (isinstance(query.conversation_id, str)
                    and query.conversation_id.startswith(("telegram:-", "household:")))):
            return ()
        if (not isinstance(selected_refs, tuple) or len(selected_refs) > 32
                or any(not isinstance(ref, tuple) or len(ref) != 3
                       or any(not isinstance(value, str) or not value or len(value) > 1024
                              for value in ref) for ref in selected_refs)):
            raise ValueError("selected private memory source references are invalid")
        binding = query.scope.get("observation_binding_sha256")
        if binding is not None and (not isinstance(binding, str)
                                    or not re.fullmatch(r"[0-9a-f]{64}", binding)):
            raise ValueError("selected private source observation binding is invalid")
        if query.project_id is not None and (not isinstance(query.project_id, str)
                                             or not query.project_id.strip()
                                             or len(query.project_id) > 200):
            raise ValueError("selected private source project is invalid")
        refs = tuple(dict.fromkeys(ref for ref in selected_refs if ref[0] in {owner, alias}))
        source_keys = tuple(dict.fromkeys((ref[0], ref[1]) for ref in refs))[:3]
        refs = tuple(ref for ref in refs if (ref[0], ref[1]) in source_keys)
        if not refs:
            return ()
        return self._read(now=now, limit=3, selected_refs=refs, selected_query=query)

    def _read(self, *, now: datetime, limit: int, before: str | None = None,
              source_id: str | None = None, context_conversations=None, after=None,
              context_voice=False, exclude_conversation=None, history_query=None,
              selected_refs=None, selected_query: RecallCurrentFacts | None = None):
        cursor = memory_source_page_request(limit, before)
        if now.tzinfo is None or now.utcoffset() != timedelta(0) or now.microsecond:
            raise ValueError("memory source time must be whole-second UTC")
        stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        e, c = operational_events.c, operational_checkpoints.c
        data = e.payload["data"]
        audience = data["audience"]
        with self._engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            with conn.begin():
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                # Local dashboard authority and conversation storage have distinct
                # namespaces on an existing installation. Never infer one from the other.
                PostgresOperationalTimeline(self._engine, self._authority_scope)._scope(conn)
                authority = require_private_owner_binding(
                    conn, scope=self._authority_scope, owner=self._owner,
                    binding=self._binding, now=now,
                )
                alias = None if self._bindings is None else self._bindings.private_bridge_principal(
                    principal_id=str(self._owner), principal_binding_id=self._binding
                )
                if (authority.provider_id, authority.channel_id) not in {
                    ("telegram", "private"), ("eva-dashboard", "owner-local"),
                }:
                    raise PeopleMemoryOverviewError("owner bridge provider is not verified")
                owners = [str(self._owner)] + ([alias] if alias else [])
                private = sa.or_(
                    sa.and_(audience["kind"].astext == "private_person",
                            audience["principal_id"].astext == data["principal_id"].astext),
                    sa.not_(data.has_key("audience")),
                )
                owned = data["principal_id"].astext == str(self._owner)
                if alias:
                    owned = sa.or_(owned, sa.and_(data["principal_id"].astext == alias,
                                                  data["conversation_id"].astext == alias))
                query = sa.select(e.event_id, e.occurred_at, e.payload, e.payload_sha256,
                                  c.phase).select_from(operational_events.join(
                    operational_checkpoints, sa.and_(e.tenant_id == c.tenant_id,
                        e.installation_id == c.installation_id, e.thread_id == c.thread_id)
                )).where(
                    e.tenant_id == self._scope.tenant_id,
                    e.installation_id == self._scope.installation_id,
                    e.event_type == "MEMORY_RETAIN_QUEUED", e.sequence == 1,
                    e.thread_id.startswith("memory-retain:"),
                    e.occurred_at <= now, owned, private,
                    # Completed retain checkpoints stay completed after redaction.
                    # The erasure receipt, not their old phase, hides the source.
                    sa.not_(data.has_key("content_erasure")),
                    data["tenant_id"].astext == self._tenant,
                    data["installation_id"].astext == self._installation,
                    sa.not_(data["conversation_id"].astext.startswith("telegram:-")),
                    c.phase.in_(("memory_queued", "memory_retaining", "memory_retry",
                                 "memory_retained", "memory_needs_help")),
                    sa.or_(data["retention_until"].astext.is_(None),
                           data["retention_until"].astext > stamp),
                )
                if context_conversations is not None:
                    channel_scope = data["conversation_id"].astext.in_(context_conversations)
                    if context_voice:
                        # Only the already verified owner and private audience above
                        # can contribute speech context. A household device cannot.
                        channel_scope = sa.or_(channel_scope, sa.and_(
                            data["principal_id"].astext == str(self._owner),
                            data["conversation_id"].astext.startswith("voice:")))
                    query = query.where(channel_scope, data["project_id"].astext.is_(None))
                    if exclude_conversation is not None:
                        query = query.where(data["conversation_id"].astext != exclude_conversation)
                    if after is not None:
                        query = query.where(e.occurred_at > after)
                if history_query is not None:
                    # A canonical private owner may use dashboard/voice; its
                    # Telegram alias must remain bound to the exact private chat.
                    history_channels = sa.and_(data["principal_id"].astext == str(self._owner),
                        sa.or_(data["conversation_id"].astext == f"dashboard:{self._binding}",
                               data["conversation_id"].astext.startswith("voice:")))
                    if alias is not None:
                        history_channels = sa.or_(history_channels, sa.and_(
                            data["principal_id"].astext == alias,
                            data["conversation_id"].astext == alias))
                    dictionary = sa.literal_column("'dutch'::regconfig")
                    terms = sa.func.plainto_tsquery(dictionary, history_query)
                    reply = operational_events.alias("private_history_reply")
                    reply_data = reply.c.payload["data"]
                    # Search only the exact reply paired with this still-retained
                    # private source. Presentation revalidates the journal reply.
                    reply_match = sa.exists(sa.select(1).select_from(reply).where(
                        reply.c.tenant_id == e.tenant_id,
                        reply.c.installation_id == e.installation_id,
                        reply.c.thread_id == sa.literal("conversation:") + data["source_ref"].astext,
                        reply.c.event_type == "REPLY_COMMITTED",
                        reply.c.occurred_at <= now,
                        reply_data["kind"].astext == "conversation_reply",
                        reply_data["principal_id"].astext == data["principal_id"].astext,
                        reply_data["conversation_id"].astext == data["conversation_id"].astext,
                        reply_data["message_id"].astext == data["source_ref"].astext,
                        reply_data["project_id"].astext.is_not_distinct_from(data["project_id"].astext),
                        sa.not_(reply_data.has_key("content_erasure")),
                        sa.func.to_tsvector(dictionary,
                            sa.func.coalesce(data["content"].astext, "") + sa.literal(" ")
                            + sa.func.coalesce(reply_data["text"].astext, "")).op("@@")(terms),
                    ).correlate(operational_events))
                    query = query.where(history_channels, sa.or_(
                        sa.func.to_tsvector(dictionary, sa.func.coalesce(data["content"].astext, "")).op("@@")(terms),
                        reply_match))
                ingress = None
                if selected_refs is not None:
                    assert selected_query is not None
                    ingress = operational_events.alias("selected_private_ingress")
                    original = ingress.c.payload["data"]
                    binding = selected_query.scope.get("observation_binding_sha256")
                    query = query.join(ingress, sa.and_(
                        ingress.c.tenant_id == e.tenant_id,
                        ingress.c.installation_id == e.installation_id,
                        ingress.c.thread_id == sa.literal("conversation:") + data["source_ref"].astext,
                        ingress.c.sequence == 1, ingress.c.event_type == "INGRESS_COMMITTED",
                        ingress.c.occurred_at <= now,
                        original["kind"].astext == "conversation_ingress",
                        original["principal_id"].astext == data["principal_id"].astext,
                        original["conversation_id"].astext == data["conversation_id"].astext,
                        original["message_id"].astext == data["source_ref"].astext,
                        original["project_id"].astext.is_not_distinct_from(selected_query.project_id),
                        sa.not_(original.has_key("content_erasure")),
                        original["text"].astext.is_not(None),
                    )).add_columns(
                        ingress.c.event_id.label("ingress_event_id"),
                        ingress.c.payload.label("ingress_payload"),
                        ingress.c.payload_sha256.label("ingress_payload_sha256"),
                        ingress.c.occurred_at.label("ingress_occurred_at"),
                    ).where(
                        sa.tuple_(data["principal_id"].astext, data["source_ref"].astext,
                                  data["evidence_ref"].astext).in_(selected_refs),
                        data["project_id"].astext.is_not_distinct_from(selected_query.project_id),
                        data["privacy_class"].astext.in_(selected_query.allowed_privacy_classes),
                        data["occurred_at"].astext <= stamp,
                        sa.not_(data["conversation_id"].astext.startswith("household:")),
                    )
                    if binding is not None:
                        query = query.where(
                            data["evidence_ref"].astext == f"observation-binding:{binding}",
                            original["observation_binding"]["binding_sha256"].astext == binding,
                        )
                    else:
                        query = query.where(sa.not_(data["evidence_ref"].astext.startswith("observation-binding:")))
                    # Several selected facts can share one original ingress. Choose
                    # one still-admitted source proof per ingress, never a nearby turn.
                    query = query.distinct(ingress.c.event_id).order_by(
                        ingress.c.event_id, e.occurred_at.desc(), e.event_id.desc())
                if source_id is not None:
                    query = query.where(e.event_id == UUID(source_id))
                if cursor:
                    query = query.where(sa.tuple_(e.occurred_at, e.event_id) < cursor)
                PostgresOperationalTimeline(self._engine, self._scope)._scope(conn)
                if selected_refs is None:
                    query = query.order_by(e.occurred_at.desc(), e.event_id.desc())
                rows = conn.execute(query.limit(limit + 1)).all()
                sources = []
                for row in rows[:limit]:
                    if canonical_sha256(row.payload) != bytes(row.payload_sha256).hex():
                        raise PeopleMemoryOverviewError("memory source hash differs")
                    doc = conversation_memory_document_from_outbox_data(row.payload["data"])
                    if (doc.principal_id not in owners or doc.audience.kind != "private_person"
                            or doc.audience.principal_id != doc.principal_id
                            or doc.tenant_id != self._tenant or doc.installation_id != self._installation
                            or (doc.retention_until is not None and doc.retention_until <= stamp)):
                        raise PeopleMemoryOverviewError("memory source scope differs")
                    state = ("retained" if row.phase == "memory_retained" else
                             "needs_help" if row.phase == "memory_needs_help" else "pending")
                    if selected_refs is not None:
                        assert selected_query is not None
                        if (doc.project_id != selected_query.project_id
                                or doc.privacy_class not in selected_query.allowed_privacy_classes
                                or doc.occurred_at > stamp
                                or (doc.principal_id == alias and doc.conversation_id != alias)
                                or (doc.principal_id, doc.source_ref, doc.evidence_ref) not in selected_refs):
                            raise PeopleMemoryOverviewError("selected private source scope differs")
                        if canonical_sha256(row.ingress_payload) != bytes(row.ingress_payload_sha256).hex():
                            raise PeopleMemoryOverviewError("selected private ingress hash differs")
                        original = row.ingress_payload.get("data")
                        if (not isinstance(original, dict) or original.get("kind") != "conversation_ingress"
                                or original.get("principal_id") != doc.principal_id
                                or original.get("conversation_id") != doc.conversation_id
                                or original.get("message_id") != doc.source_ref
                                or original.get("project_id") != doc.project_id
                                or "content_erasure" in original
                                or not isinstance(original.get("text"), str) or not original["text"].strip()
                                or row.ingress_occurred_at > now):
                            raise PeopleMemoryOverviewError("selected private ingress scope differs")
                        binding = selected_query.scope.get("observation_binding_sha256")
                        if binding is not None:
                            original_binding = original.get("observation_binding")
                            if (doc.evidence_ref != f"observation-binding:{binding}"
                                    or not isinstance(original_binding, dict)
                                    or original_binding.get("binding_sha256") != binding):
                                raise PeopleMemoryOverviewError("selected private source observation binding differs")
                        text = original["text"]
                        truncated = len(text) > 4096
                        if truncated:
                            suffix = "\n[Fragment; overige oorspronkelijke tekst niet opgenomen.]"
                            text = text[:4096 - len(suffix)] + suffix
                        sources.append({"source_ref": doc.source_ref, "evidence_ref": doc.evidence_ref,
                            "principal_id": doc.principal_id, "project_id": doc.project_id,
                            "occurred_at": row.ingress_occurred_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "text": text, "truncated": truncated})
                        continue
                    if history_query is not None:
                        sources.append((doc, state))
                        continue
                    if source_id is not None or context_conversations is not None:
                        return doc, state
                    sources.append(MemorySource(str(row.event_id),
                        row.occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        doc.content[:2048], len(doc.content) > 2048, state))
                if selected_refs is not None:
                    order = {key: index for index, key in enumerate(dict.fromkeys(
                        (ref[0], ref[1]) for ref in selected_refs))}
                    return tuple(sorted(sources, key=lambda item: order[(item["principal_id"], item["source_ref"])]))
                if history_query is not None:
                    return tuple(sources)
                if source_id is not None or context_conversations is not None:
                    return None
                next_cursor = None
                if len(rows) > limit:
                    last = sources[-1]
                    next_cursor = f"{last.occurred_at}|{last.source_id}"
                return MemorySourcePage(tuple(sources), next_cursor)


_MAX_THREADS_PER_TRACK = 256
_EXPIRY_WARNING = timedelta(hours=24)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PostgresPeopleMemoryOverview:
    """Read only the durable status cursors and discard every identifying value."""

    def __init__(self, engine: Engine, *, tenant_id: str, installation_id: str) -> None:
        if not tenant_id.strip() or not installation_id.strip():
            raise ValueError("people and memory overview scope is required")
        self._tenant_id = tenant_id
        self._installation_id = installation_id
        self._timeline = PostgresOperationalTimeline(
            engine,
            TimelineScope(
                tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
                installation_id=uuid5(
                    NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"
                ),
            ),
        )

    def snapshot(
        self, *, now: datetime, hindsight_state: HindsightState
    ) -> PeopleMemoryOverview:
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise ValueError("people and memory snapshot time must be UTC")
        if now.microsecond:
            raise ValueError("people and memory snapshot time must use whole seconds")
        return PeopleMemoryOverview(
            dynamic_account=self._safe_dynamic_account(),
            group_sharing=self._safe_group_sharing(now),
            memory_flow=self._safe_memory_flow(),
            hindsight=hindsight_state,
        )

    def _validated_threads(
        self, prefix: str
    ) -> tuple[tuple[CheckpointState, tuple[TimelineEvent, ...]], ...]:
        states = self._timeline.list_checkpoints(
            thread_prefix=prefix,
            limit=_MAX_THREADS_PER_TRACK,
        )
        if len(states) == _MAX_THREADS_PER_TRACK:
            raise PeopleMemoryOverviewError(
                "people and memory overview track exceeded its closed bound"
            )
        validated = []
        for listed in states:
            current = self._timeline.load(listed.thread_id)
            if current != listed:
                raise PeopleMemoryOverviewError(
                    "people and memory checkpoint changed during projection"
                )
            events = self._timeline.events(listed.thread_id)
            if (
                not events
                or tuple(event.sequence for event in events)
                != tuple(range(1, len(events) + 1))
                or len(events) != current.revision
            ):
                raise PeopleMemoryOverviewError(
                    "people and memory timeline differs from its checkpoint"
                )
            if any(
                later.occurred_at < earlier.occurred_at
                for earlier, later in zip(events, events[1:], strict=False)
            ):
                raise PeopleMemoryOverviewError(
                    "people and memory timeline time moved backwards"
                )
            for event in events:
                self._event_data(event)
            validated.append((current, events))
        return tuple(validated)

    @staticmethod
    def _event_data(event: TimelineEvent) -> dict[str, object]:
        if set(event.payload) != {"checkpoint", "data"}:
            raise PeopleMemoryOverviewError(
                "people and memory event payload is malformed"
            )
        checkpoint = event.payload.get("checkpoint")
        data = event.payload.get("data")
        if not isinstance(checkpoint, dict) or not isinstance(data, dict):
            raise PeopleMemoryOverviewError(
                "people and memory event payload is malformed"
            )
        if set(checkpoint) != {
            "checkpoint_id",
            "deadline",
            "last_error_fingerprint",
            "operation_id",
            "phase",
            "retry_count",
            "revision",
            "status",
            "updated_at",
        }:
            raise PeopleMemoryOverviewError(
                "people and memory event checkpoint is malformed"
            )
        if (
            checkpoint["checkpoint_id"] != event.checkpoint_id
            or checkpoint["operation_id"] != event.operation_id
            or checkpoint["revision"] != event.sequence
            or checkpoint["updated_at"]
            != event.occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ")
            or checkpoint["deadline"] is not None
        ):
            raise PeopleMemoryOverviewError(
                "people and memory event checkpoint differs"
            )
        return data

    @staticmethod
    def _require_event_state(
        event: TimelineEvent,
        *,
        phase: str,
        status: str,
        retry_count: int,
        last_error_fingerprint: str | None = None,
    ) -> None:
        checkpoint = event.payload["checkpoint"]
        if not isinstance(checkpoint, dict) or (
            checkpoint.get("phase") != phase
            or checkpoint.get("status") != status
            or checkpoint.get("retry_count") != retry_count
            or checkpoint.get("last_error_fingerprint")
            != last_error_fingerprint
        ):
            raise PeopleMemoryOverviewError(
                "people and memory event state is malformed"
            )

    def _safe_dynamic_account(self) -> DynamicAccountState:
        try:
            threads = self._validated_threads("telegram-dynamic-binding:")
            for state, events in threads:
                first = events[0]
                data = self._event_data(first)
                principal_id = data.get("principal_id")
                telegram_user_id = data.get("telegram_user_id")
                if not isinstance(principal_id, str) or not isinstance(
                    telegram_user_id, int
                ):
                    raise PeopleMemoryOverviewError(
                        "dynamic Telegram binding data is malformed"
                    )
                binding = TelegramDynamicBinding(telegram_user_id, principal_id)
                operation_id = (
                    f"telegram-dynamic-binding:{binding.telegram_user_id}"
                )
                if (
                    state.phase != "bound"
                    or state.status != "completed"
                    or len(events) != 1
                    or first.event_type != "TELEGRAM_DYNAMIC_BINDING_CREATED"
                    or set(data) != {"principal_id", "telegram_user_id"}
                    or state.thread_id
                    != f"telegram-dynamic-binding:{binding.telegram_user_id}"
                    or state.operation_id != operation_id
                    or first.operation_id != operation_id
                ):
                    raise PeopleMemoryOverviewError(
                        "dynamic Telegram binding status is malformed"
                    )
                self._require_event_state(
                    first, phase="bound", status="completed", retry_count=0
                )
            return "extra_account_linked" if threads else "no_extra_account"
        except Exception:
            return "unavailable"

    def _safe_group_sharing(self, now: datetime) -> GroupSharingState:
        try:
            threads = self._validated_threads("telegram-group-consent:")
            active_expiries: list[datetime] = []
            for state, events in threads:
                if events[0].event_type != "TELEGRAM_GROUP_CONSENT_GRANTED":
                    raise PeopleMemoryOverviewError(
                        "Telegram group consent does not start with a grant"
                    )
                latest_consent: TelegramGroupConsent | None = None
                principal_id: str | None = None
                group_chat_id: str | None = None
                last_was_grant = False
                for index, event in enumerate(events):
                    data = self._event_data(event)
                    event_principal = data.get("principal_id")
                    event_group = data.get("group_chat_id")
                    if not isinstance(event_principal, str) or not isinstance(
                        event_group, str
                    ):
                        raise PeopleMemoryOverviewError(
                            "Telegram group consent binding is malformed"
                        )
                    if index == 0:
                        principal_id = event_principal
                        group_chat_id = event_group
                    if (
                        event_principal != principal_id
                        or event_group != group_chat_id
                        or state.thread_id
                        != f"telegram-group-consent:{principal_id}:{group_chat_id}"
                    ):
                        raise PeopleMemoryOverviewError(
                            "Telegram group consent binding is malformed"
                        )
                    operation_id = event.operation_id
                    if not isinstance(operation_id, str):
                        raise PeopleMemoryOverviewError(
                            "Telegram group consent operation is malformed"
                        )
                    TelegramGroupConsentReference(
                        principal_id=event_principal,
                        group_chat_id=event_group,
                        operation_id=operation_id,
                    )
                    if event.event_type in {
                        "TELEGRAM_GROUP_CONSENT_GRANTED",
                        "TELEGRAM_GROUP_CONSENT_RENEWED",
                    }:
                        if (
                            index > 0
                            and event.event_type == "TELEGRAM_GROUP_CONSENT_GRANTED"
                        ) or set(data) != {
                            "expires_at",
                            "granted_at",
                            "group_chat_id",
                            "principal_id",
                        }:
                            raise PeopleMemoryOverviewError(
                                "active Telegram group consent is malformed"
                            )
                        latest_consent = TelegramGroupConsent(
                            event_group,
                            event_principal,
                            self._parse_timestamp(data.get("granted_at")),
                            self._parse_timestamp(data.get("expires_at")),
                        )
                        self._require_event_state(
                            event,
                            phase="group_memory_shared",
                            status="active",
                            retry_count=0,
                        )
                        last_was_grant = True
                    elif event.event_type == "TELEGRAM_GROUP_CONSENT_REVOKED":
                        if set(data) != {"group_chat_id", "principal_id"}:
                            raise PeopleMemoryOverviewError(
                                "revoked Telegram group consent is malformed"
                            )
                        self._require_event_state(
                            event,
                            phase="group_memory_not_shared",
                            status="completed",
                            retry_count=0,
                        )
                        last_was_grant = False
                    else:
                        raise PeopleMemoryOverviewError(
                            "Telegram group consent event is unknown"
                        )
                if (
                    last_was_grant
                    and latest_consent is not None
                    and latest_consent.permits(
                        principal_id=latest_consent.principal_id,
                        group_chat_id=latest_consent.group_chat_id,
                        now=now,
                    )
                ):
                    active_expiries.append(latest_consent.expires_at)
            if not active_expiries:
                return "private_only"
            if any(expiry - now <= _EXPIRY_WARNING for expiry in active_expiries):
                return "expires_soon"
            return "group_active"
        except Exception:
            return "unavailable"

    def _safe_memory_flow(self) -> MemoryFlowState:
        try:
            threads = self._validated_threads("memory-retain:")
            observed: set[MemoryFlowState] = set()
            for state, events in threads:
                observed.add(self._validate_memory_timeline(state, events))
            priorities: tuple[MemoryFlowState, ...] = (
                "needs_help",
                "retrying",
                "processing",
                "up_to_date",
            )
            for priority in priorities:
                if priority in observed:
                    return priority
            return "no_signals"
        except Exception:
            return "unavailable"

    def _validate_memory_timeline(
        self, state: CheckpointState, events: tuple[TimelineEvent, ...]
    ) -> MemoryFlowState:
        first = events[0]
        queued_data = self._event_data(first)
        document = conversation_memory_document_from_outbox_data(queued_data)
        operation_id = str(uuid5(NAMESPACE_URL, document.document_id))
        if (
            first.event_type != "MEMORY_RETAIN_QUEUED"
            or document.tenant_id != self._tenant_id
            or document.installation_id != self._installation_id
            or state.thread_id
            != f"memory-retain:{hashlib.sha256(document.document_id.encode()).hexdigest()}"
            or any(event.operation_id != operation_id for event in events)
        ):
            raise PeopleMemoryOverviewError("memory retention admission is malformed")
        self._require_event_state(
            first, phase="memory_queued", status="active", retry_count=0
        )

        attempt = 0
        prior_type = first.event_type
        last_error: str | None = None
        result: MemoryFlowState = "processing"
        for event in events[1:]:
            data = self._event_data(event)
            if event.event_type == "MEMORY_RETAIN_ATTEMPTED":
                if prior_type not in {
                    "MEMORY_RETAIN_QUEUED",
                    "MEMORY_RETAIN_RETRY_SCHEDULED",
                }:
                    raise PeopleMemoryOverviewError(
                        "memory retention attempt is out of order"
                    )
                attempt += 1
                if (
                    attempt > 3
                    or set(data) != {"attempt", "document_id"}
                    or data.get("attempt") != attempt
                    or data.get("document_id") != document.document_id
                ):
                    raise PeopleMemoryOverviewError(
                        "memory retention attempt is malformed"
                    )
                self._require_event_state(
                    event,
                    phase="memory_retaining",
                    status="waiting_for_proof",
                    retry_count=attempt,
                    last_error_fingerprint=last_error,
                )
                result = "processing"
            elif event.event_type == "MEMORY_RETAIN_SUCCEEDED":
                if prior_type != "MEMORY_RETAIN_ATTEMPTED" or set(data) != {
                    "bank_id",
                    "content_sha256",
                    "document_id",
                }:
                    raise PeopleMemoryOverviewError(
                        "memory retention success is out of order"
                    )
                receipt = MemoryRetentionReceipt(
                    bank_id=data.get("bank_id"),  # type: ignore[arg-type]
                    document_id=data.get("document_id"),  # type: ignore[arg-type]
                    content_sha256=data.get("content_sha256"),  # type: ignore[arg-type]
                )
                if (
                    receipt.document_id != document.document_id
                    or receipt.content_sha256
                    != hashlib.sha256(document.content.encode()).hexdigest()
                ):
                    raise PeopleMemoryOverviewError(
                        "memory retention receipt differs"
                    )
                self._require_event_state(
                    event,
                    phase="memory_retained",
                    status="completed",
                    retry_count=attempt,
                )
                result = "up_to_date"
            elif event.event_type == "MEMORY_RETAIN_RETRY_SCHEDULED":
                error_fingerprint = data.get("error_fingerprint")
                if (
                    prior_type != "MEMORY_RETAIN_ATTEMPTED"
                    or attempt >= 3
                    or set(data)
                    != {"document_id", "error_fingerprint", "retry_count"}
                    or data.get("document_id") != document.document_id
                    or data.get("retry_count") != attempt
                    or not isinstance(error_fingerprint, str)
                    or not _SHA256.fullmatch(error_fingerprint)
                ):
                    raise PeopleMemoryOverviewError(
                        "memory retention retry is malformed"
                    )
                last_error = error_fingerprint
                self._require_event_state(
                    event,
                    phase="memory_retry",
                    status="failed",
                    retry_count=attempt,
                    last_error_fingerprint=last_error,
                )
                result = "retrying"
            elif event.event_type == "MEMORY_RETAIN_NEEDS_HELP":
                if prior_type != "MEMORY_RETAIN_ATTEMPTED" or attempt != 3:
                    raise PeopleMemoryOverviewError(
                        "memory retention terminal state is out of order"
                    )
                failure_keys = {"document_id", "error_fingerprint", "retry_count"}
                restart_keys = {"document_id", "reason"}
                if set(data) == failure_keys:
                    error_fingerprint = data.get("error_fingerprint")
                    if (
                        data.get("document_id") != document.document_id
                        or data.get("retry_count") != 3
                        or not isinstance(error_fingerprint, str)
                        or not _SHA256.fullmatch(error_fingerprint)
                    ):
                        raise PeopleMemoryOverviewError(
                            "memory retention terminal failure is malformed"
                        )
                    last_error = error_fingerprint
                elif set(data) == restart_keys:
                    if (
                        data.get("document_id") != document.document_id
                        or data.get("reason")
                        != "retry_budget_exhausted_after_ambiguous_restart"
                    ):
                        raise PeopleMemoryOverviewError(
                            "memory retention terminal restart is malformed"
                        )
                else:
                    raise PeopleMemoryOverviewError(
                        "memory retention terminal state is malformed"
                    )
                self._require_event_state(
                    event,
                    phase="memory_needs_help",
                    status="needs_help",
                    retry_count=3,
                    last_error_fingerprint=last_error,
                )
                result = "needs_help"
            else:
                raise PeopleMemoryOverviewError(
                    "memory retention event is unknown"
                )
            prior_type = event.event_type
        return result

    @staticmethod
    def _parse_timestamp(value: object) -> datetime:
        if not isinstance(value, str):
            raise PeopleMemoryOverviewError("group consent expiry is malformed")
        try:
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=UTC
            )
        except ValueError as error:
            raise PeopleMemoryOverviewError(
                "group consent expiry is malformed"
            ) from error
        return parsed


__all__ = ["PostgresPeopleMemoryOverview"]
