"""Deliver saved public research to its native HA satellite once.

This is a host output adapter, not a new model tool or private speaker identity.
A service acknowledgement means offered, never proof of audible completion.
"""
import logging
import sqlalchemy as sa
import httpx2 as httpx
from eva.adapters.postgres.operational_timeline import operational_events, operational_checkpoints
from eva.application.work_result_presentation import readable_work_result
from eva.application.work_failure_notice import FAILURE_STATES

_LOG = logging.getLogger(__name__)


class NativeWorkDelivery:
    def __init__(self, server, provider):
        self.server, self.provider = server, provider

    def _sessions(self):
        store = self.server.store
        with store._engine.begin() as c:
            store._scope(c)
            rows = c.execute(sa.select(operational_events.c.payload,
                    operational_checkpoints.c.phase, operational_checkpoints.c.deadline).join(
                operational_checkpoints,
                (operational_events.c.tenant_id == operational_checkpoints.c.tenant_id)
                & (operational_events.c.installation_id == operational_checkpoints.c.installation_id)
                & (operational_events.c.thread_id == operational_checkpoints.c.thread_id)).where(
                operational_events.c.tenant_id == store.scope.tenant_id,
                operational_events.c.installation_id == store.scope.installation_id,
                operational_events.c.event_type == "HOUSEHOLD_WORK_SESSION_OPENED",
                operational_checkpoints.c.phase == "open",
                operational_checkpoints.c.deadline > self.server.authority.clock(),
                operational_events.c.payload["data"]["native_conversation_key"].astext.is_not(None),
            ).order_by(operational_events.c.occurred_at.desc(), operational_events.c.thread_id.desc())
                .limit(128)).all()
        # A later wakeword conversation does not cancel already admitted public
        # work. Keep its original session/device authority until it closes/expires.
        for row in rows:
            binding = row.payload["data"]["binding"]
            if row.phase == "open" and self.server.authority.clock() < row.deadline:
                yield binding

    @staticmethod
    def _idle(client, satellite):
        response = client.get("/api/states/" + satellite)
        response.raise_for_status()
        state = response.json()
        return state.get("entity_id") == satellite and state.get("state") == "idle"

    def run_once(self):
        self.server.authority.validate_current()
        idle_satellites = {}
        for binding in self._sessions():
            claim = None
            service = None
            turn = None
            try:
                service = self.server.session(token=self.server.authority.voice.token,
                    satellite_id=binding["satellite_id"], device_id=binding["device_id"],
                    session_id=binding["session_id"])
                turn = service.control_turn()
                # Only the configured HA endpoint and one exact admitted satellite.
                with httpx.Client(base_url=self.provider.endpoint_url.rstrip("/"),
                        headers={"Authorization": "Bearer " + self.provider.credential},
                        timeout=4, follow_redirects=False) as client:
                    satellite = binding["satellite_id"]
                    if satellite not in idle_satellites:
                        idle_satellites[satellite] = self._idle(client, satellite)
                    if not idle_satellites[satellite]:
                        continue
                    claim = service.poll(turn)
                    if claim is None:
                        continue
                    work = service._load(claim["work_id"], service.origin(turn))
                    text = readable_work_result(claim["result"])
                    if len(text) > 4000:
                        text = text[:4000].rsplit(" ", 1)[0] + (
                            " Dit was het eerste deel. Vraag me naar het volledige resultaat.")
                    message = text if work.state in FAILURE_STATES else "Over je opdracht " + work.title + ": " + text
                    authorized = service.result_delivery(turn, "authorize", work_id=claim["work_id"],
                        result_sha256=claim["result_sha256"])
                    if authorized is None:
                        return
                    if not self._idle(client, binding["satellite_id"]):
                        service.result_delivery(turn, "defer", work_id=claim["work_id"],
                            result_sha256=claim["result_sha256"])
                        return
                    service.current()
                    response = client.post("/api/services/assist_satellite/announce",
                        json={"entity_id": binding["satellite_id"], "message": message,
                              "preannounce": False})
                    response.raise_for_status()
                service.result_delivery(turn, "ack", work_id=claim["work_id"],
                    result_sha256=claim["result_sha256"], disposition="offered")
                return
            except Exception:
                # No exception text: transport errors may include credentials or content.
                _LOG.warning("Native household result delivery unavailable")
                if claim is not None:
                    try:
                        service.result_delivery(turn, "ack", work_id=claim["work_id"],
                            result_sha256=claim["result_sha256"], disposition="ambiguous")
                    except Exception:
                        pass
                    return
