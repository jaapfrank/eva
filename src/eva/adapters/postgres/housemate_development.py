"""Current reference-only development selection on the existing operational lane."""
from __future__ import annotations

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    insert_initial_timeline_thread, transition_timeline_thread,
    operational_checkpoints, operational_events,
)
from eva.application.housemate_development import SELECTION_CONTRACT, validate_development_selection
from eva.contracts.canonical_json import canonical_sha256


class PostgresDevelopmentSelection:
    def __init__(self, owner):
        self.owner = owner
        self.engine, self.scope = owner._engine, owner._scope
        self.thread = "eva-development:" + canonical_sha256(str(owner._owner))

    def _latest(self, connection):
        c, e = operational_checkpoints, operational_events
        row = connection.execute(sa.select(c.c.revision, c.c.phase, c.c.status, c.c.updated_at,
            e.c.payload, e.c.payload_sha256).join(e, sa.and_(
                c.c.tenant_id == e.c.tenant_id, c.c.installation_id == e.c.installation_id,
                c.c.thread_id == e.c.thread_id, c.c.revision == e.c.sequence)).where(
                c.c.tenant_id == self.scope.tenant_id,
                c.c.installation_id == self.scope.installation_id,
                c.c.thread_id == self.thread)).mappings().one_or_none()
        if row is None:
            return None
        payload = row["payload"]
        if (canonical_sha256(payload) != bytes(row["payload_sha256"]).hex()
                or any(payload.get("checkpoint", {}).get(key) != row[key]
                       for key in ("revision", "phase", "status"))
                or payload.get("checkpoint", {}).get("updated_at") != row["updated_at"].strftime("%Y-%m-%dT%H:%M:%SZ")):
            raise RuntimeError("development current checkpoint differs")
        data = payload["data"]
        if (set(data) != {"selection", "selection_sha256"}
                or canonical_sha256(data["selection"]) != data["selection_sha256"]
                or data["selection"].get("contract") != SELECTION_CONTRACT
                or data["selection"].get("owner_id") != str(self.owner._owner)):
            raise RuntimeError("development source selection differs")
        validate_development_selection(data["selection"])
        return {"revision":row["revision"], **data}

    def load(self, *, now):
        with self.engine.begin() as connection:
            self.owner._authorize(connection, now)
            return self._latest(connection)

    def current_projects(self, references, *, now):
        """Read only the at-most-two selected projects, without artifact/review scans."""
        if not isinstance(references, list) or len(references) > 2:
            raise ValueError("development project reference bound differs")
        threads = ["intent:" + ref["intent_id"] for ref in references]
        if not threads:
            return []
        with self.engine.begin() as connection:
            self.owner._authorize(connection, now)
            rows = connection.execute(self.owner._query().where(
                operational_checkpoints.c.thread_id.in_(threads)).limit(2)).all()
            cards = []
            for row in rows:
                data = row.adoption["data"]
                status = ("stopped" if row.phase == "personal_stopped" else
                    "done" if row.phase in {"release_ready", "initiative_completed"} else
                    "attention" if row.status in {"failed", "needs_help"} else "working")
                cards.append({"intent_id":data["intent_id"], "title":data["title"],
                    "motivation":data["motivation"], "question":data["next_exploration"],
                    "status":status, "phase":row.phase, "updated_at":row.updated_at.isoformat()})
            return cards

    def save(self, selection, *, now):
        validate_development_selection(selection)
        if (selection.get("contract") != SELECTION_CONTRACT
                or selection.get("owner_id") != str(self.owner._owner)):
            raise ValueError("development selection owner differs")
        digest = canonical_sha256(selection)
        with self.engine.begin() as connection:
            self.owner._authorize(connection, now)
            connection.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:lane, 0))"),
                {"lane": self.thread})
            previous = self._latest(connection)
            if previous is not None and previous["selection_sha256"] == digest:
                return False
            # Nothing processed is not an invented first experience.
            if previous is None and not selection["delta_refs"] and not selection["project_refs"]:
                return False
            revision = 1 if previous is None else previous["revision"] + 1
            arguments = dict(thread_id=self.thread, checkpoint_id=f"{self.thread}:r{revision}",
                phase="development_current", status="completed", event_type="DEVELOPMENT_SOURCES_PROCESSED",
                occurred_at=now, data={"selection":selection, "selection_sha256":digest})
            if previous is None:
                insert_initial_timeline_thread(connection, self.scope, **arguments)
            else:
                transition_timeline_thread(connection, self.scope,
                    expected_revision=previous["revision"], **arguments)
            return True
