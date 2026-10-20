"""Reuse retained, proven interpretations from the existing conversation ledger.

This is a disposable index over Eva's original admissions, not another memory
or execution store. Every hit resolves its target again and dispatches a new
durable operation. Missing source/context evidence is an ordinary cache miss.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
import hashlib
import threading
from uuid import uuid5, NAMESPACE_URL

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import operational_events as events, operational_checkpoints as checkpoints, external_timeline_scope
from eva.adapters.postgres.scope import PostgresScopedRole
from eva.application.conversation import ConversationActionCall, ConversationTurn
from eva.contracts.canonical_json import canonical_sha256


_SETTERS = ("set_home_assistant_light", "set_home_assistant_switch")
_VERSION = "ha-exact-command-reuse-v1"


def turn_scope(turn):
    return canonical_sha256({"tenant": turn.tenant_id, "installation": turn.installation_id,
        "principal": turn.principal_id, "audience": asdict(turn.audience),
        "project": turn.project_id})


class _Ledger(PostgresScopedRole):
    _role = "eva_runtime"

    def __init__(self, engine, turn):
        self._engine = engine
        self._scope_value = external_timeline_scope(turn.tenant_id, turn.installation_id, "Command reuse requires installation scope")

    @contextmanager
    def read(self):
        with self._engine.connect().execution_options(isolation_level="REPEATABLE READ") as c:
            with c.begin():
                self._scope(c)
                yield c

    def scoped(self):
        return (events.c.tenant_id == self._scope_value.tenant_id,
                events.c.installation_id == self._scope_value.installation_id)

    @staticmethod
    def checked(row):
        if canonical_sha256(row["payload"]) != bytes(row["payload_sha256"]).hex():
            raise ValueError("Command cache source hash changed")
        return row["payload"]["data"]

    def frame(self, c, since):
        data = events.c.payload["data"]
        rows = c.execute(sa.select(events).where(*self.scoped(),
            events.c.event_type == "INGRESS_COMMITTED", events.c.occurred_at >= since)
            .order_by(events.c.occurred_at.desc(), events.c.event_id).limit(65)).mappings().all()
        if len(rows) > 64:
            return None
        counts = c.execute(sa.select(sa.func.count(),
            sa.func.count().filter(data.has_key("content_erasure"))).where(*self.scoped(),
            events.c.event_type == "INGRESS_COMMITTED")).one()
        erased = c.execute(sa.select(sa.func.count(), sa.func.coalesce(sa.func.sum(checkpoints.c.revision), 0))
            .where(checkpoints.c.tenant_id == self._scope_value.tenant_id,
                   checkpoints.c.installation_id == self._scope_value.installation_id,
                   checkpoints.c.thread_id.like("memory-storage:%"),
                   checkpoints.c.phase.in_(("memory_storage_removing", "memory_storage_removed", "memory_storage_remove_needs_help")))).one()
        for row in rows:
            self.checked(row)
        return {"ingress_count": counts[0], "erasure_epoch": [counts[1], int(erased[0]), int(erased[1])],
                "rows": rows}


@dataclass(frozen=True)
class RetainedCommand:
    call: ConversationActionCall
    source_turn: ConversationTurn
    source_thread_id: str
    admission: dict
    metadata: dict


class HomeAssistantCommandCache:
    def __init__(self, engine, *, configuration_sha256, device_context, capability_profile):
        self._engine = engine
        self._configuration = configuration_sha256
        self._devices = device_context
        self._profile = capability_profile
        self._index = OrderedDict()
        self._seeded = OrderedDict()
        self._index_lock = threading.RLock()

    def _remember_key(self, key):
        with self._index_lock:
            self._index[key] = True
            self._index.move_to_end(key)
            while len(self._index) > 256:
                self._index.popitem(last=False)

    def _known(self, turn):
        scope = turn_scope(turn)
        with self._index_lock:
            if scope not in self._seeded:
                ledger = _Ledger(self._engine, turn)
                meta = events.c.payload["data"]["interpretation_reuse"]
                with ledger.read() as c:
                    hashes = c.execute(sa.select(meta["input_sha256"].astext).where(*ledger.scoped(),
                        events.c.event_type == "TOOL_REQUEST_ADMITTED",
                        meta["configuration_sha256"].astext == self._configuration,
                        meta["turn_scope_sha256"].astext == scope,
                        events.c.occurred_at >= datetime.now(UTC) - timedelta(hours=24))
                        .order_by(events.c.occurred_at.desc()).limit(256)).scalars().all()
                for digest in reversed(hashes):
                    if isinstance(digest, str):
                        self._remember_key((scope, digest))
                self._seeded[scope] = True
                while len(self._seeded) > 256:
                    self._seeded.popitem(last=False)
            return (scope, hashlib.sha256(turn.text.encode()).hexdigest()) in self._index

    @staticmethod
    def _ordinary(turn):
        from eva.application.initiative_execution import require_initiative_context
        return (turn.project_id is None and turn.observation_binding is None
                and turn.media_context is None and turn.media_context_sha256 is None
                and turn.audience.kind in {"private_person", "household"}
                and require_initiative_context(turn) is None)

    def _binding(self, turn):
        snapshot = self._devices.binding_snapshot()
        profile = self._profile.snapshot()
        stable_profile = {k: v for k, v in profile.items() if k not in {"observed_at", "profile_sha256"}}
        return {"version": _VERSION, "configuration_sha256": self._configuration,
                "turn_scope_sha256": turn_scope(turn),
                "device_context_sha256": snapshot["binding_sha256"],
                "capability_sha256": canonical_sha256(stable_profile)}

    def admission_metadata(self, turn, call, preparation):
        if (call.name not in _SETTERS
                or call.arguments.get("reusable_command") is not True
                or call.arguments.get("finish_without_reply") is not True or not self._ordinary(turn)):
            return None
        binding = self._binding(turn)
        ledger = _Ledger(self._engine, turn)
        since = datetime.fromisoformat(turn.occurred_at.replace("Z", "+00:00"))
        with ledger.read() as c:
            frame = ledger.frame(c, since)
            if frame is None:
                return None
            others = [r for r in frame["rows"] if ledger.checked(r).get("message_id") != turn.message_id]
            if (not any(ledger.checked(r).get("message_id") == turn.message_id for r in frame["rows"])
                    or any(not self._independent_command(c, ledger, ledger.checked(r)) for r in others)):
                return None
        digest = hashlib.sha256(turn.text.encode()).hexdigest()
        self._remember_key((binding["turn_scope_sha256"], digest))
        return {**binding, "input_sha256": digest,
            "ingress_count": frame["ingress_count"], "erasure_epoch": frame["erasure_epoch"],
            "baseline_event_ids": [str(r["event_id"]) for r in frame["rows"]]}

    def lookup(self, turn):
        if not self._ordinary(turn) or not self._known(turn):
            return None
        ledger = _Ledger(self._engine, turn)
        admission = events.alias("cached_admission")
        ingress = events.alias("cached_ingress")
        a, s = admission.c.payload["data"], ingress.c.payload["data"]
        # Exact retained text and source scope; no fuzzy/semantic or cross-user match.
        with ledger.read() as c:
            row = c.execute(sa.select(admission.c.thread_id, admission.c.payload,
                admission.c.payload_sha256, ingress.c.payload.label("source_payload"),
                ingress.c.payload_sha256.label("source_hash"), ingress.c.occurred_at.label("source_at"))
                .select_from(admission.join(ingress, sa.and_(admission.c.tenant_id == ingress.c.tenant_id,
                    admission.c.installation_id == ingress.c.installation_id,
                    a["source_message_id"].astext == s["message_id"].astext)))
                .where(admission.c.tenant_id == ledger._scope_value.tenant_id,
                    admission.c.installation_id == ledger._scope_value.installation_id,
                    admission.c.event_type == "TOOL_REQUEST_ADMITTED", ingress.c.event_type == "INGRESS_COMMITTED",
                    a["action_name"].astext.in_(_SETTERS), s["text"].astext == turn.text,
                    s["principal_id"].astext == turn.principal_id,
                    a["interpretation_reuse"]["configuration_sha256"].astext == self._configuration,
                    a["interpretation_reuse"]["turn_scope_sha256"].astext == turn_scope(turn),
                    ingress.c.occurred_at >= datetime.now(UTC) - timedelta(hours=24),
                    s["message_id"].astext != turn.message_id)
                .order_by(ingress.c.occurred_at.desc(), admission.c.event_id).limit(1)).mappings().first()
            if row is None:
                return None
            source = row["source_payload"]["data"]
            if canonical_sha256(row["source_payload"]) != bytes(row["source_hash"]).hex():
                return None
            stored = ledger.checked(row)
            meta = stored.get("interpretation_reuse")
            if not isinstance(meta, dict) or meta.get("version") != _VERSION:
                return None
            replies = c.execute(sa.select(events).where(*ledger.scoped(),
                events.c.thread_id == "conversation:" + source["message_id"],
                events.c.event_type == "REPLY_COMMITTED")).mappings().all()
            if len(replies) != 1:
                return None
            reply = ledger.checked(replies[0])
            if (reply.get("response_mode") != "silent" or not reply.get("text")
                    or reply.get("content_erasure") or reply.get("content_compaction")
                    or reply.get("provider") not in {"eva:verified-action", "eva:verified-reuse"}
                    or any(reply.get(k) != source.get(k) for k in ("principal_id", "conversation_id", "message_id"))):
                return None
            args = stored.get("action_arguments")
            if (not isinstance(args, dict) or args.get("reusable_command") is not True
                    or args.get("finish_without_reply") is not True
                    or meta.get("input_sha256") != hashlib.sha256(turn.text.encode()).hexdigest()):
                return None
            source_turn = ConversationTurn(tenant_id=turn.tenant_id, installation_id=turn.installation_id,
                principal_id=turn.principal_id, audience=turn.audience, project_id=None,
                conversation_id=source["conversation_id"], message_id=source["message_id"], text=turn.text,
                occurred_at=row["source_at"].astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
            candidate = RetainedCommand(ConversationActionCall(str(uuid5(NAMESPACE_URL,
                "eva:reuse:" + turn.message_id + ":" + canonical_sha256(args))), stored["action_name"], args),
                source_turn, row["thread_id"], stored, meta)
            if not self._context_valid(c, ledger, candidate, turn):
                return None
        binding = self._binding(turn)
        return candidate if all(meta.get(k) == v for k, v in binding.items()) else None

    def _context_valid(self, c, ledger, candidate, turn):
        meta = candidate.metadata
        since = datetime.fromisoformat(candidate.source_turn.occurred_at.replace("Z", "+00:00"))
        frame = ledger.frame(c, since)
        if frame is None or frame["erasure_epoch"] != meta.get("erasure_epoch"):
            return False
        baseline = set(meta.get("baseline_event_ids", []))
        new = [r for r in frame["rows"] if str(r["event_id"]) not in baseline]
        if frame["ingress_count"] != meta.get("ingress_count", -1) + len(new):
            return False  # Includes backdated ingress outside the bounded window.
        for row in new:
            data = ledger.checked(row)
            if data.get("message_id") == turn.message_id:
                continue
            if not self._independent_command(c, ledger, data):
                return False
        return True

    def _independent_command(self, c, ledger, ingress):
        """Only another proven standalone command may leave meaning unchanged."""
        data = events.c.payload["data"]
        admissions = c.execute(sa.select(events).where(*ledger.scoped(),
            events.c.event_type == "TOOL_REQUEST_ADMITTED",
            data["source_message_id"].astext == ingress.get("message_id"))).mappings().all()
        replies = c.execute(sa.select(events).where(*ledger.scoped(),
            events.c.thread_id == "conversation:" + ingress.get("message_id", ""),
            events.c.event_type == "REPLY_COMMITTED")).mappings().all()
        if len(admissions) != 1 or len(replies) != 1:
            return False
        admission, reply = ledger.checked(admissions[0]), ledger.checked(replies[0])
        args, meta = admission.get("action_arguments", {}), admission.get("interpretation_reuse", {})
        if (admission.get("action_name") not in _SETTERS or args.get("reusable_command") is not True
                or args.get("finish_without_reply") is not True or meta.get("version") != _VERSION
                or meta.get("configuration_sha256") != self._configuration
                or meta.get("input_sha256") != hashlib.sha256(ingress.get("text", "").encode()).hexdigest()
                or reply.get("response_mode") != "silent" or not reply.get("text")
                or reply.get("provider") not in {"eva:verified-action", "eva:verified-reuse"}
                or any(reply.get(k) != ingress.get(k) for k in ("principal_id", "conversation_id", "message_id"))):
            return False
        terminal = c.execute(sa.select(events).where(*ledger.scoped(),
            events.c.thread_id == admissions[0]["thread_id"])
            .order_by(events.c.sequence.desc()).limit(1)).mappings().one()
        ledger.checked(terminal)
        return terminal["event_type"] == "STATE_VERIFIED"
