"""Deliver saved household work to its original native HA satellite once.

This is a host output adapter, not a new model tool or private speaker identity.
A service acknowledgement means offered, never proof of audible completion.
"""
import logging
import sqlalchemy as sa
import httpx2 as httpx
from eva.adapters.postgres.operational_timeline import operational_events
from eva.bootstrap.household_work import pending_household_results
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
            pending = pending_household_results(c, store, self.server.authority.clock()).subquery()
            data = operational_events.c.payload["data"]
            policy = self.server.authority.policy
            current = {name: getattr(policy, name) for name in (
                "tenant_id", "installation_id", "household_id", "consent_operation_id",
                "household_policy_sha256")}
            current["research_policy_sha256"] = self.server.authority.sha256
            device_bindings = [data["binding"].contains({"satellite_id": satellite,
                "device_id": device, "actor_id": "household-device:" + device})
                for satellite, device in policy.devices.items()]
            rows = c.execute(sa.select(operational_events.c.payload).where(
                operational_events.c.tenant_id == store.scope.tenant_id,
                operational_events.c.installation_id == store.scope.installation_id,
                operational_events.c.event_type == "HOUSEHOLD_WORK_SESSION_OPENED",
                operational_events.c.sequence == 1,
                data["native_conversation_key"].astext.is_not(None),
                data["binding"].contains(current), sa.or_(*device_bindings),
                sa.exists(sa.select(1).select_from(pending).where(
                    pending.c.spec_payload["origin"]["session_id"].astext
                    == data["binding"]["session_id"].astext,
                    pending.c.spec_payload["owner_principal_id"].astext == str(policy.owner_principal_id),
                    pending.c.spec_payload["principal_binding_id"].astext == str(policy.principal_binding_id))),
            ).order_by(operational_events.c.occurred_at, operational_events.c.thread_id)
                .limit(128)).all()
        for row in rows:
            yield row.payload["data"]["binding"]

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
                service = self.server.delivery_session(token=self.server.authority.voice.token,
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
