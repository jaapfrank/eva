"""Failure intake using the existing owner initiative and development execution."""
from collections import deque
import hashlib
import logging
from pathlib import Path

import sqlalchemy as sa

from eva.application.conversation import GroundedReply, ConversationStopped
from eva.application.self_repair import RepairObservation
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec

_LOG = logging.getLogger(__name__)


def compose_self_repair(runtime):
    service = runtime._delegated_work_owner_service
    if service is None or service.digital_profile is None:
        return None
    authority = runtime._initiative_authority()
    try:
        policy, _ = authority.policy()
    except Exception:
        return None
    if policy.get('self_repair', {}).get('enabled') is not True:
        return None
    from eva.adapters.postgres.self_repair import PostgresSelfRepair
    return _ScheduledFailureRepair(PostgresSelfRepair(authority, service))


def _recovery_marker_matches(marker, work):
    spec = work.spec
    if (type(spec) is not DelegatedDigitalWorkSpec or spec.generation != 1 or spec.audience != "private"
            or work.state != "ambiguous" or work.error_code != "provider_unavailable"
            or work.stop_requested_at is not None or work.lease_owner is not None
            or work.lease_expires_at is not None or work.call_id is None
            or work.call_started_at is None or work.result is not None):
        return False
    payload = marker["payload"]
    if not isinstance(payload, dict):
        return False
    data, checkpoint = payload.get("data"), payload.get("checkpoint")
    prefix = f"scheduled-work:{spec.owner_principal_id}:provider-recovery:"
    schedule = prefix + hashlib.sha256(spec.execution_id.encode()).hexdigest()
    return (
        marker["thread_id"] == schedule
        and marker["checkpoint_id"] == schedule + ":blocked"
        and marker["work_id"] == spec.work_id
        and bytes(marker["spec_sha256"]).hex() == spec.sha256
        and canonical_sha256(payload) == bytes(marker["payload_sha256"]).hex()
        and isinstance(checkpoint, dict)
        and checkpoint.get("checkpoint_id") == marker["checkpoint_id"]
        and checkpoint.get("revision") == 1
        and checkpoint.get("phase") == "blocked"
        and checkpoint.get("status") == "needs_help"
        and isinstance(data, dict)
        and set(data) == {"operation", "work_id", "run_at", "reason", "blocker_code", "automatic_retry"}
        and data["operation"] == "resume"
        and data["work_id"] == spec.work_id
        and data["automatic_retry"] is False
    )


class _ScheduledFailureRepair:
    """Observe one current delivery failure on the existing repair service tick."""
    def __init__(self, repair):
        from eva.adapters.postgres.scheduled_work_failure import ScheduledFailureSource

        self.repair = repair
        self.service = repair.service
        self.observe = repair.observe
        self.loader = ScheduledFailureSource(self.service.store)
        self._after = ""
        self._observed = deque(maxlen=32)

    def _markers(self):
        from eva.adapters.postgres.current_catalog import delegated_work_runs
        from eva.adapters.postgres.operational_timeline import operational_checkpoints, operational_events

        store = self.service.store
        c, e, w = operational_checkpoints.c, operational_events.c, delegated_work_runs.c
        successor = operational_events.alias("scheduled_failure_successor").c
        return sa.select(c.thread_id, c.checkpoint_id, e.payload, e.payload_sha256, w.work_id, w.spec_sha256).select_from(
            operational_checkpoints
        ).join(
            operational_events, sa.and_(e.tenant_id == c.tenant_id, e.installation_id == c.installation_id,
                                        e.thread_id == c.thread_id, e.sequence == 1,
                                        e.checkpoint_id == c.checkpoint_id, e.occurred_at == c.updated_at)
        ).join(delegated_work_runs, sa.and_(w.tenant_id == c.tenant_id, w.installation_id == c.installation_id,
                                          w.work_id == e.payload["data"]["work_id"].astext)).where(
            c.tenant_id == store.scope.tenant_id, c.installation_id == store.scope.installation_id,
            c.thread_id.startswith(f"scheduled-work:{self.service.owner_principal_id}:provider-recovery:"),
            c.revision == 1, c.phase == "blocked", c.status == "needs_help", c.operation_id.is_(None),
            e.event_type == "SCHEDULED_WORK_BLOCKED",
            ~sa.exists(sa.select(1).where(
                successor.tenant_id == c.tenant_id, successor.installation_id == c.installation_id,
                successor.thread_id == c.thread_id, successor.sequence > 1,
            )),
            w.owner_principal_id == self.service.owner_principal_id,
            w.principal_binding_id == self.service.principal_binding_id,
            w.spec_payload["contract"].astext == "DelegatedDigitalWork/v4",
            w.state == "ambiguous", w.error_code == "provider_unavailable", w.stop_requested_at.is_(None),
            w.lease_owner.is_(None), w.lease_expires_at.is_(None),
        )

    def _privacy_source(self, spec, connection):
        from eva.adapters.postgres.operational_timeline import (
            PostgresOperationalTimeline, external_timeline_scope, operational_events,
        )

        source = spec.source
        scope = self.service.conversation_scope
        if external_timeline_scope(source.tenant_id, source.installation_id,
                                   "scheduled repair requires its private source scope") != scope:
            return None
        PostgresOperationalTimeline(self.service.store._engine, scope)._scope(connection)
        e = operational_events.c
        row = connection.execute(sa.select(e.event_id, e.payload, e.payload_sha256).where(
            e.tenant_id == scope.tenant_id, e.installation_id == scope.installation_id,
            e.thread_id == "conversation:" + source.message_id, e.event_type == "INGRESS_COMMITTED",
        )).one_or_none()
        if row is None:
            return None
        privacy = {key: getattr(source, key) for key in ("principal_id", "conversation_id", "message_id")}
        data = row.payload.get("data", {})
        digest = bytes(row.payload_sha256)
        if ("content_compaction" in data or "content_erasure" in data
                or any(data.get(key) != value for key, value in privacy.items())
                or canonical_sha256(row.payload) != digest.hex()
                or canonical_sha256(data) != spec.source_ingress_sha256
                or connection.scalar(sa.text("SELECT eva_core.lock_conversation_source(:event_id,:digest)"),
                                     {"event_id": row.event_id, "digest": digest}) is not True):
            return None
        return {**privacy, "ingress_sha256": digest.hex()}

    @staticmethod
    def _lock_marker(connection, query, schedule_id):
        from eva.adapters.postgres.current_catalog import delegated_work_runs
        from eva.adapters.postgres.operational_timeline import operational_checkpoints

        return connection.execute(query.where(operational_checkpoints.c.thread_id == schedule_id)
            .with_for_update(read=True, of=(operational_checkpoints, delegated_work_runs),
                             skip_locked=True)).mappings().one_or_none()

    def _failure_observation(self, schedule_id):
        try:
            self.loader.load(schedule_id)
        except ValueError as error:
            if type(error) is not ValueError or error.args != ("scheduled failure history differs",):
                return None
            observation = RepairObservation.from_error(error, source_root=Path(__file__).resolve().parents[1])
            if any(path == "adapters/postgres/scheduled_work_failure.py" and function.startswith("load:")
                   for path, function, _ in observation.frames):
                return observation
        return None

    def _has_recovery_stop(self, connection, stop_thread):
        from eva.adapters.postgres.operational_timeline import operational_events

        store = self.service.store
        e = operational_events.c
        return connection.scalar(sa.select(sa.literal(True)).where(
            e.tenant_id == store.scope.tenant_id, e.installation_id == store.scope.installation_id,
            e.thread_id == stop_thread,
        ).limit(1)) is True

    def _observe_stopped_failure(self, query, marker, spec):
        from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline
        from eva.adapters.postgres.self_repair import HOST_SCHEDULED_FAILURE_SOURCE

        store = self.service.store
        schedule_id = marker["thread_id"]
        stop_thread = store._provider_recovery_stop_thread(spec)
        with store._engine.begin() as source_connection, store._engine.begin() as connection:
            # Hold the privacy anchor and work proof on their own database
            # scopes. No old conversation turn is reconstructed or authorized.
            privacy = self._privacy_source(spec, source_connection)
            if privacy is None:
                return True, None
            store._scope(connection)
            if self._lock_marker(connection, query, schedule_id) != marker:
                return True, None
            if not self._has_recovery_stop(connection, stop_thread):
                return False, None
            state, stops = PostgresOperationalTimeline(store._engine, store.scope).load_with_events(stop_thread)
            if (len(stops) != 1 or state.checkpoint_id != stop_thread + ":stopped"
                    or state.phase != "stopped" or state.status != "completed"
                    or stops[0].event_type != "PRIVATE_WORK_RECOVERY_STOPPED"
                    or stops[0].payload.get("data") != {"execution_id": spec.execution_id,
                        "source_ingress_sha256": spec.source_ingress_sha256,
                        "work_id": spec.work_id, "spec_sha256": spec.sha256}):
                return True, None
            observation = self._failure_observation(schedule_id)
            if observation is None:
                return True, None
            provenance = {
                "schedule_id": schedule_id, "recovery_stop_thread": stop_thread, "work_id": spec.work_id,
                "scheduled_marker_sha256": bytes(marker["payload_sha256"]).hex(),
                "predecessor_spec_sha256": spec.sha256, "recovery_stop_sha256": stops[0].payload_sha256,
                "source_ingress_data_sha256": spec.source_ingress_sha256,
            }
            source = {
                "contract": HOST_SCHEDULED_FAILURE_SOURCE, "observation_sha256": observation.fingerprint,
                "privacy_source": privacy, "technical_provenance": provenance,
                "technical_provenance_sha256": canonical_sha256(provenance),
            }
            return True, self.repair.observe_host_scheduled_failure(
                source_connection, observation, source
            )

    def _observe_one(self):
        from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
        from eva.adapters.postgres.operational_timeline import operational_checkpoints
        from eva.adapters.postgres.work_source_retention import retained_source_guard

        store = self.service.store
        c = operational_checkpoints.c
        query = self._markers()
        with store._engine.begin() as connection:
            store._scope(connection)
            marker = connection.execute(query.where(c.thread_id > self._after)
                                        .order_by(c.thread_id).limit(1)).mappings().one_or_none()
        self._after = marker["thread_id"] if marker is not None else ""
        if marker is None or bytes(marker["payload_sha256"]) in self._observed:
            return
        work = store.load(marker["work_id"], **self.service._owner)
        if not _recovery_marker_matches(marker, work):
            return
        stopped, result = self._observe_stopped_failure(query, marker, work.spec)
        if stopped:
            if result is not None:
                self._observed.append(bytes(marker["payload_sha256"]))
            return

        # A current ingress may still authorize technical fault observation after
        # the predecessor's work retention expires. No task is resumed or retained.
        with retained_source_guard(self.service, work.spec) as turn:
            self.service.validate_authority(turn)
            PostgresConversationJournal(store._engine, self.service.conversation_scope).ensure_running(turn)
        with store._engine.begin() as connection:
            store._scope(connection)
            current = self._lock_marker(connection, query, marker["thread_id"])
            if current != marker:
                return
            # Terminal provider failures retain their immutable work row. STOP
            # instead appends this execution fence while holding the work lock.
            if self._has_recovery_stop(connection, store._provider_recovery_stop_thread(work.spec)):
                return
            observation = self._failure_observation(marker["thread_id"])
            # The existing observer rechecks the original ingress and STOP
            # while committing its source-bound, deduplicated repair incident.
            if observation is not None and self.observe(turn, observation) is not None:
                self._observed.append(bytes(marker["payload_sha256"]))

    def run_once(self):
        state = self.repair.run_once()
        if state == "idle":
            try:
                self._observe_one()
            except Exception:
                _LOG.debug("Scheduled failure repair intake unavailable")
        return state


def repair_failed_turn(runtime, turn, error):
    repair = getattr(runtime, '_self_repair', None)
    from eva.adapters.projection.hindsight import HindsightProjectionUnavailable
    from eva.application.cancellation import WorkInterrupted
    if repair is None or isinstance(error, (ConversationStopped, WorkInterrupted, HindsightProjectionUnavailable)):
        return None
    try:
        observation = RepairObservation.from_error(error,
            source_root=Path(__file__).resolve().parents[1])
        journal = runtime._journal(turn)
        with journal.execution(turn):
            # A concurrent saved answer remains authoritative. No original
            # request or uncertain external effect is dispatched again.
            committed = journal.committed_reply(turn)
            if committed is not None:
                return committed
            result = repair.observe(turn, observation)
            if result is None or not result['created']:
                return None
            obs_ledger = getattr(runtime, '_household_observation', None)
            if obs_ledger is not None:
                try:
                    obs_ledger.record_runtime_fault(critical=True, repaired=False)
                except Exception:
                    pass
            text = ('Ik ben tegen een probleempje aangelopen. Ik ga uitzoeken wat er misgaat '
                    'en het bij mezelf herstellen.' if result['admitted'] else
                    'Ik ben tegen een probleempje aangelopen. Ik heb het vastgelegd en pak '
                    'het herstel op zodra mijn lopende werk klaar is.')
            return journal.commit_reply(turn, GroundedReply(
                conversation_id=turn.conversation_id, message_id=turn.message_id,
                text=text, evidence_memory_ids=(), generated_at=turn.occurred_at,
                provider='runtime:self-repair-admitted', model_usage=journal.model_usage(turn)))
    except Exception:
        # Preserve the original failure when incident persistence/authority is
        # unavailable. Never recursively repair the intake itself or promise work.
        _LOG.debug('Automatic repair intake unavailable')
        return None


def observe_failed_work(store, service, source, spec, error, *, clock):
    """Capture the actual caught error before the existing worker returns failure.

    This does not replay the original work, convert failure to success or start
    another model. The same durable repair intake later claims the native lane.
    """
    from eva.adapters.projection.hindsight import HindsightProjectionUnavailable
    from eva.contracts.delegated_digital_work import DelegatedInitiativeDigitalWorkSpec
    if (isinstance(spec, DelegatedInitiativeDigitalWorkSpec) or source is None
            or isinstance(error, HindsightProjectionUnavailable)
            or source.audience.kind != 'private_person' or source.project_id is not None):
        return None
    try:
        authority=store._initiative_authority_loader()
        from eva.adapters.postgres.self_repair import PostgresSelfRepair, record_failed_work_repair_notice
        observation=RepairObservation.from_error(error,source_root=Path(__file__).resolve().parents[1])
        result=PostgresSelfRepair(authority,service,clock=clock).observe(source,observation)
        if result is not None and result['created']:
            record_failed_work_repair_notice(store,spec,result,now=clock())
        return result
    except Exception:
        _LOG.debug('Automatic work repair intake unavailable')
        return None
