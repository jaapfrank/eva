"""Bounded public household continuity from existing canonical journals only."""
from datetime import timedelta
from dataclasses import replace
import logging
import sqlalchemy as sa
from eva.adapters.postgres.operational_timeline import operational_events as events
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.scoped_delegated_work_actions import ScopedDelegatedWorkActions
from eva.application.conversation import RecentConversationMessage
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.timestamps import parse_utc_timestamp

_LOG = logging.getLogger(__name__)


def _valid(row):
    return bytes(row["payload_sha256"]).hex() == canonical_sha256(row["payload"])


class HouseholdConversationContext:
    def __init__(self, server):
        self.server = server

    def recent(self, turn, *, limit=6, activity=False, offset=0, local_date=None):
        if turn.audience.kind != "household" or turn.project_id is not None:
            return ()
        if type(limit) is not int or not 1 <= limit <= (20 if activity else 6) or type(offset) is not int or not 0 <= offset <= 1000:
            raise ValueError("household conversation bound invalid")
        if local_date is not None and not activity:
            raise ValueError("calendar selection requires activity reader")
        server, store = self.server, self.server.store
        service = ScopedDelegatedWorkActions(None, server)._select(turn)._service
        service.validate_authority(turn)
        now = server.authority.clock()
        if activity:
            now = min(now, parse_utc_timestamp(turn.occurred_at))
        start, end, calendar_end = now - timedelta(hours=24), now, None
        metadata = {}
        if activity:
            from eva.bootstrap.recent_activity import activity_window
            start, end, calendar_end, zone = activity_window(now, local_date)
            metadata = {
                "pagination_consistency": "Window pinned to this request; retention or erasure changes can remove records between pages.",
                "window_start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "window_end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "window_end_exclusive": calendar_end is not None and end == calendar_end,
                "local_date": local_date, "timezone": zone,
                "coverage": "Retained authorized public action events recorded in this window, grouped by original request. Includes earlier requests with action events in this window; ongoing actions without a recorded event in the window may be absent. Session/page bounds and erasure may omit records. Delegated work, when included, is a separate bounded list not filtered to this window.",
                "result_fragment_character_budget": 6000,
                "session_candidate_limit": 65,
            }
        current = service.binding
        data = events.c.payload["data"]
        binding = data["binding"]
        devices = [binding.contains({"satellite_id": satellite, "device_id": device,
            "actor_id": "household-device:" + device})
            for satellite, device in server.authority.policy.devices.items()]
        with store._engine.begin() as c:
            store._scope(c)
            sessions = c.execute(sa.select(events).where(
                events.c.tenant_id == store.scope.tenant_id,
                events.c.installation_id == store.scope.installation_id,
                events.c.event_type == "HOUSEHOLD_WORK_SESSION_OPENED", events.c.sequence == 1,
                events.c.occurred_at >= now - timedelta(hours=25) if not activity else sa.true(),
                events.c.occurred_at <= end,
                events.c.occurred_at < calendar_end if calendar_end is not None else sa.true(),
                binding.contains({key: current[key] for key in (
                    "tenant_id", "installation_id", "household_id", "consent_operation_id",
                    "household_policy_sha256", "research_policy_sha256")}),
                sa.or_(*devices) if devices else sa.false(),
            ).order_by(events.c.occurred_at.desc(), events.c.thread_id.desc()).limit(65)).mappings().all()
            identity_threads = ["voice-identity:" + row["payload"]["data"]["binding"]["session_id"]
                for row in sessions if _valid(row)]
            confirmations = c.execute(sa.select(events).where(
                events.c.tenant_id == store.scope.tenant_id,
                events.c.installation_id == store.scope.installation_id,
                events.c.thread_id.in_(identity_threads),
                events.c.event_type == "VOICE_IDENTITY_CONFIRMED")).mappings().all() if identity_threads else []
            by_identity = {}
            for confirmation in confirmations:
                by_identity.setdefault(confirmation["thread_id"], []).append(confirmation)
            admitted = {}
            from eva.adapters.postgres.household_work_sessions import session_thread
            for row in sessions:
                if not _valid(row):
                    continue
                source = row["payload"]["data"]["binding"]
                if row["thread_id"] != session_thread(source["session_id"]):
                    continue
                boundary = end
                for confirmation in by_identity.get("voice-identity:" + source["session_id"], ()):
                    if not _valid(confirmation):
                        boundary = None
                        break
                    stamp = confirmation["payload"]["data"].get("speaker_confirmed_at")
                    boundary = min(boundary, parse_utc_timestamp(stamp) if stamp
                                   else confirmation["occurred_at"])
                if boundary is not None:
                    admitted[source["conversation_id"]] = (source, boundary)
        if not admitted:
            service.validate_authority(turn)
            return {"records": [], "next_offset": None, "page_limit_reached": False,
                    "session_limit_reached": len(sessions) == 65, **metadata} if activity else ()
        journal = PostgresConversationJournal(store._engine, server.conversation_scope)
        scope = server.conversation_scope
        clauses = [sa.and_(data["conversation_id"].astext == conversation,
            data["principal_id"].astext == source["actor_id"], events.c.occurred_at <= boundary)
            for conversation, (source, boundary) in admitted.items()]
        source_filter = sa.or_(*clauses)
        selected_from = events
        activity_at = events.c.occurred_at
        if activity:
            from eva.bootstrap.recent_activity import ACTIVITY_EVENTS
            from eva.adapters.postgres.operational_timeline import external_timeline_scope
            tool_scope = external_timeline_scope(turn.tenant_id, turn.installation_id, "activity scope required")
            tools = events.alias("activity_tools")
            changes = events.alias("activity_changes")
            tool_data = tools.c.payload["data"]
            # Resolve the authorized action window once, then join its exact
            # source requests. A correlated scan for every historical ingress
            # repeatedly traverses the same tool journal and dominates latency.
            allowed = sa.values(sa.column("conversation_id", sa.Text),
                sa.column("principal_id", sa.Text), sa.column("boundary", sa.DateTime(timezone=True)),
                name="activity_authority").data([
                    (conversation, source["actor_id"], boundary)
                    for conversation, (source, boundary) in admitted.items()])
            source_message = tool_data["source_message_id"].astext
            source_conversation = tool_data["conversation_id"].astext
            source_principal = tool_data["principal_id"].astext
            activity_sources = sa.select(source_message.label("message_id"),
                source_conversation.label("conversation_id"), source_principal.label("principal_id"),
                allowed.c.boundary, sa.func.max(changes.c.occurred_at).label("activity_at")
            ).select_from(tools.join(allowed, sa.and_(
                source_conversation == allowed.c.conversation_id,
                source_principal == allowed.c.principal_id)).join(changes,
                sa.and_(changes.c.tenant_id == tools.c.tenant_id,
                    changes.c.installation_id == tools.c.installation_id,
                    changes.c.thread_id == tools.c.thread_id))).where(
                tools.c.tenant_id == tool_scope.tenant_id,
                tools.c.installation_id == tool_scope.installation_id,
                tools.c.event_type == "TOOL_REQUEST_ADMITTED",
                tools.c.occurred_at <= end,
                tools.c.occurred_at <= allowed.c.boundary,
                tools.c.occurred_at < calendar_end if calendar_end is not None else sa.true(),
                tool_data["execution_namespace"].astext.is_(None),
                changes.c.event_type.in_(ACTIVITY_EVENTS),
                changes.c.occurred_at >= start, changes.c.occurred_at <= end,
                changes.c.occurred_at < calendar_end if calendar_end is not None else sa.true(),
                changes.c.occurred_at <= allowed.c.boundary,
            ).group_by(source_message, source_conversation, source_principal,
                       allowed.c.boundary).subquery("public_activity")
            selected_from = events.join(activity_sources, sa.and_(
                events.c.thread_id == sa.literal("conversation:") + activity_sources.c.message_id,
                data["message_id"].astext == activity_sources.c.message_id,
                data["conversation_id"].astext == activity_sources.c.conversation_id,
                data["principal_id"].astext == activity_sources.c.principal_id))
            source_filter = events.c.occurred_at <= activity_sources.c.boundary
            activity_at = activity_sources.c.activity_at
        with store._engine.begin() as c:
            journal._scope(c)
            ingress = c.execute(sa.select(events, activity_at.label("activity_at")).select_from(selected_from).where(
                events.c.tenant_id == scope.tenant_id, events.c.installation_id == scope.installation_id,
                events.c.event_type == "INGRESS_COMMITTED", events.c.sequence == 1,
                events.c.occurred_at >= start if not activity else sa.true(),
                events.c.occurred_at <= end,
                events.c.occurred_at < calendar_end if calendar_end is not None else sa.true(),
                data["project_id"].astext.is_(None), data["observation_binding"].is_(None),
                data["message_id"].astext != turn.message_id,
                data["text"].astext.is_not(None),
                data["content_erasure"].is_(None),
                sa.true() if activity else data["conversation_id"].astext != turn.conversation_id,
                source_filter,
            ).order_by(activity_at.desc(), events.c.thread_id.desc()).offset(offset).limit(limit + (1 if activity else 0))).mappings().all()
            more = len(ingress) > limit
            ingress = ingress[:limit]
            replies = c.execute(sa.select(events).where(
                events.c.tenant_id == scope.tenant_id, events.c.installation_id == scope.installation_id,
                events.c.event_type == "REPLY_COMMITTED",
                events.c.thread_id.in_([row["thread_id"] for row in ingress]),
            )).mappings().all() if ingress else []
        replies = {row["thread_id"]: row for row in replies if _valid(row)}
        # Fully verify only the bounded selected sources, rather than issuing
        # per-session queries across the complete candidate window.
        used = {row["payload"]["data"]["conversation_id"] for row in ingress}
        for conversation in used:
            source = admitted[conversation][0]
            verified = server.sessions.read_retained_household(source["session_id"],
                source["satellite_id"], source["device_id"])
            if verified != source:
                raise ValueError("household conversation source binding changed")
        result = []
        activities = []
        content_budget = [6000]
        for row in reversed(ingress):
            if not _valid(row):
                continue
            value = row["payload"]["data"]
            source = admitted[value["conversation_id"]][0]
            message_id = value.get("message_id", "")
            if (row["thread_id"] != "conversation:" + message_id
                    or not message_id.startswith(source["conversation_id"] + ":request:")):
                continue
            text = value.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            if activity:
                from eva.bootstrap.recent_activity import foreground_activity
                activities.append({
                    "occurred_at": row["occurred_at"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "activity_at": row["activity_at"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "request_fragment": text[:600],
                    "request_truncated": len(text) > 600,
                    "source_sha256": bytes(row["payload_sha256"]).hex(),
                    "actions": foreground_activity(store._engine, turn, value,
                        admitted[value["conversation_id"]][1], calendar_end=calendar_end,
                        window_start=start, content_budget=content_budget),
                })
            digest = canonical_sha256({"conversation_id": value["conversation_id"], "message_id": message_id})
            session_ref = canonical_sha256({"conversation_id": value["conversation_id"]})[:16]
            session_origin = ("andere sessie " if value["conversation_id"] != turn.conversation_id
                              else "huidige sessie ") + session_ref
            provenance = ("Eerder openbaar huisgesprek via " + source["satellite_id"]
                + "; " + session_origin
                + "; identiteit van de huidige gesprekspartner niet vastgesteld voor deze bron; "
                "geen nieuwe opdracht.\n")
            result.append(RecentConversationMessage("Onbekende spreker", provenance + text[:1200],
                row["occurred_at"].strftime("%Y-%m-%dT%H:%M:%SZ"), source_turn_sha256=digest))
            reply = replies.get(row["thread_id"])
            if reply is None:
                continue
            answer = reply["payload"]["data"]
            if (any(answer.get(key) != value.get(key) for key in ("conversation_id", "principal_id", "message_id", "project_id"))
                    or not isinstance(answer.get("text"), str) or not answer["text"].strip()):
                continue
            result.append(RecentConversationMessage("Eva", provenance + answer["text"][:1200],
                reply["occurred_at"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                playback_status="unconfirmed", source_turn_sha256=digest))
        service.validate_authority(turn)
        if activity:
            return {"records": activities, "next_offset": offset + limit if more and offset + limit <= 1000 else None,
                "page_limit_reached": more and offset + limit > 1000,
                "session_limit_reached": len(sessions) == 65, **metadata}
        return tuple(result)


def with_household_continuity(runtime, turn, recent):
    reader = getattr(runtime, "_household_conversation_context", None)
    if reader is None or turn.audience.kind != "household" or turn.project_id is not None:
        return recent
    try:
        stored = reader.recent(turn)
    except Exception:
        _LOG.debug("Shared household conversation context unavailable")
        return recent
    # Preserve actual current-session playback information over saved model
    # output. A source-turn digest binds both sides of the exchange.
    seen = {item.source_turn_sha256 for item in recent if item.source_turn_sha256}
    additions = [item for item in stored if item.source_turn_sha256 not in seen]
    budget = 6000
    selected = []
    for item in reversed(additions):
        if budget <= 0:
            break
        text = item.text[:budget]
        selected.append(replace(item, text=text))
        budget -= len(text)
    return tuple((*reversed(selected), *recent)[-24:])
