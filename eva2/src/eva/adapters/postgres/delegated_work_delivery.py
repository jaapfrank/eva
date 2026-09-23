"""Atomic destination sidecars on the existing operational timeline."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
import re

import sqlalchemy as sa
from eva.application.work_failure_notice import delivery_outcome, WorkFailureNotice
from eva.adapters.postgres.work_failure_notice import failure_notice_cutoff, deliverable_states

from eva.adapters.postgres.operational_timeline import (
    TimelineScope, insert_initial_timeline_thread, operational_events,
    operational_checkpoints, transition_timeline_thread,
)
from eva.application.delegated_work_delivery import AuthorizedWorkDelivery, PrivateWorkDestination
from eva.adapters.postgres.current_catalog import delegated_work_runs
from eva.adapters.postgres.telegram_pairing import PostgresPrivateTelegramRecipient
from eva.application.delegated_work_delivery import PrivateWorkDeliveryClaim
from eva.contracts.timestamps import require_utc_second
from eva.application.telegram_ingress import TelegramPrincipalBindings
from eva.contracts.canonical_json import canonical_sha256


_EVENT = "DELEGATED_PRIVATE_DESTINATION_BOUND"


def _thread_id(work_id: str) -> str:
    return "delegated-destination:" + canonical_sha256({"work_id": work_id})


def _payload(spec, destination: PrivateWorkDestination) -> dict:
    if (spec.audience != "private" or str(destination.owner_principal_id) != spec.owner_principal_id
            or str(destination.principal_binding_id) != spec.principal_binding_id):
        raise ValueError("private destination differs from work owner")
    return {"contract": "EvaDelegatedWorkDestinationBinding/v1", "work_id": spec.work_id,
            "spec_sha256": spec.sha256, "destination": destination.to_payload()}


def _read(connection, scope, spec) -> PrivateWorkDestination | None:
    rows = connection.execute(sa.select(operational_events).where(
        operational_events.c.tenant_id == scope.tenant_id,
        operational_events.c.installation_id == scope.installation_id,
        operational_events.c.thread_id == _thread_id(spec.work_id),
    )).mappings().all()
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("private destination history is invalid")
    row = rows[0]
    payload = row["payload"]
    if (row["event_type"] != _EVENT or row["sequence"] != 1
            or canonical_sha256(payload) != bytes(row["payload_sha256"]).hex()):
        raise ValueError("private destination event integrity differs")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("private destination data is invalid")
    destination = PrivateWorkDestination.from_payload(data.get("destination"))
    if data != _payload(spec, destination):
        raise ValueError("private destination work binding differs")
    return destination


def record_admission_destination(connection: sa.Connection, scope: TimelineScope, *,
                                 spec, destination: PrivateWorkDestination | None,
                                 occurred_at: datetime, existing: bool) -> None:
    """Called only inside the work admission transaction and its identity lock."""
    if existing:
        if _read(connection, scope, spec) != destination:
            raise ValueError("private destination cannot be changed or backfilled")
        return
    if destination is None:
        return
    data = _payload(spec, destination)
    thread_id = _thread_id(spec.work_id)
    insert_initial_timeline_thread(connection, scope, thread_id=thread_id,
        checkpoint_id="checkpoint:" + thread_id, occurred_at=occurred_at,
        data=data, event_type=_EVENT, phase="private_destination_bound", status="completed")


class PostgresDelegatedWorkDeliveryStore:
    def __init__(self, work_store, *, bindings_loader: Callable[[], TelegramPrincipalBindings],
                 clock: Callable[[], datetime] | None = None, document_source=None):
        self._work = work_store
        self._documents = document_source
        self._bindings_loader = bindings_loader
        self._clock = clock or (lambda: datetime.now(UTC).replace(microsecond=0))

    def _current(self, work_id: str, *, owner_principal_id: UUID, principal_binding_id: UUID):
        now = self._clock()
        require_utc_second(now, "destination authority time")
        # Reuse the work store's scoped authority checks in this same transaction.
        with self._work._engine.begin() as connection:
            self._work._scope(connection)
            self._work._validate_owner_binding(connection, owner_principal_id=owner_principal_id,
                                              principal_binding_id=principal_binding_id)
            work = self._work._load_locked(connection, work_id)
            if (work.spec.audience != "private" or work.spec.owner_principal_id != str(owner_principal_id)
                    or work.spec.principal_binding_id != str(principal_binding_id)):
                from eva.adapters.postgres.delegated_work import DelegatedWorkNotFound
                raise DelegatedWorkNotFound("delegated work is absent")
            if work.retention_until <= now:
                raise ValueError("private completion result retention has expired")
            destination = _read(connection, self._work.scope, work.spec)
            if destination is not None:
                try:
                    recipient = PostgresPrivateTelegramRecipient(self._work._engine, self._work.scope,
                        owner=owner_principal_id, binding=principal_binding_id, bindings=self._bindings_loader)
                    if recipient.current_chat_id(now=now) != destination.chat_id:
                        raise ValueError("private completion recipient changed")
                    destination.validate_current(self._bindings_loader())
                except (OSError, RuntimeError, ValueError) as error:
                    raise ValueError("private completion destination is unavailable") from error
            return work, destination

    def load(self, work_id: str, *, owner_principal_id: UUID,
             principal_binding_id: UUID) -> PrivateWorkDestination | None:
        if self._documents is not None and work_id.startswith("document-task:"):
            result = self._document_authorized(work_id, owner_principal_id, principal_binding_id)
            return None if result is None else result.destination
        return self._current(work_id, owner_principal_id=owner_principal_id,
                             principal_binding_id=principal_binding_id)[1]

    def authorize_delivery(self, work_id: str, *, owner_principal_id: UUID,
                           principal_binding_id: UUID) -> AuthorizedWorkDelivery | None:
        if self._documents is not None and work_id.startswith("document-task:"):
            return self._document_authorized(work_id, owner_principal_id, principal_binding_id)
        work, destination = self._current(work_id, owner_principal_id=owner_principal_id,
                                          principal_binding_id=principal_binding_id)
        result = delivery_outcome(work)
        if destination is None or result is None:
            return None
        return AuthorizedWorkDelivery(destination, work.spec.work_id, work.spec.sha256,
            canonical_sha256(result.to_payload()), result.output,
            result.state if isinstance(result, WorkFailureNotice) else "completed")


    @staticmethod
    def _delivery_thread(work_id):
        return "delegated-delivery:" + canonical_sha256({"work_id": work_id})

    def claim_next(self, *, now: datetime, lease_seconds: int = 120) -> PrivateWorkDeliveryClaim | None:
        """Fence one send. Expired claims become ambiguous, never available again."""
        require_utc_second(now, "delivery claim time")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("delivery lease must be within 1..300 seconds")
        events, checkpoints = operational_events, operational_checkpoints
        for _ in range(16):
            with self._work._engine.begin() as connection:
                self._work._scope(connection)
                cutoff = failure_notice_cutoff(connection, self._work, now)
                joined = delegated_work_runs.join(events, sa.and_(
                    events.c.tenant_id == delegated_work_runs.c.tenant_id,
                    events.c.installation_id == delegated_work_runs.c.installation_id,
                    events.c.payload["data"]["work_id"].astext == delegated_work_runs.c.work_id,
                    events.c.event_type == _EVENT,
                )).outerjoin(checkpoints, sa.and_(
                    checkpoints.c.tenant_id == events.c.tenant_id,
                    checkpoints.c.installation_id == events.c.installation_id,
                    checkpoints.c.thread_id == sa.func.replace(events.c.thread_id,
                        "delegated-destination:", "delegated-delivery:"),
                ))
                row = connection.execute(sa.select(delegated_work_runs).select_from(joined).where(
                    delegated_work_runs.c.tenant_id == self._work.scope.tenant_id,
                    delegated_work_runs.c.installation_id == self._work.scope.installation_id,
                    deliverable_states(delegated_work_runs, cutoff),
                    delegated_work_runs.c.stop_requested_at.is_(None),
                    delegated_work_runs.c.retention_until > now,
                    sa.or_(checkpoints.c.thread_id.is_(None), sa.and_(
                        checkpoints.c.phase == "delivery_claimed", checkpoints.c.deadline <= now)),
                ).order_by(delegated_work_runs.c.created_at, delegated_work_runs.c.work_id)
                  .limit(1).with_for_update(of=delegated_work_runs, skip_locked=True)).mappings().one_or_none()
                if row is None:
                    return self._claim_document_next(now=now, lease_seconds=lease_seconds)
                work = self._work._validated(connection, row)
                destination = _read(connection, self._work.scope, work.spec)
                result = delivery_outcome(work)
                if destination is None or result is None:
                    raise ValueError("completion destination or result disappeared")
                thread = self._delivery_thread(work.spec.work_id)
                state = connection.execute(sa.select(checkpoints).where(
                    checkpoints.c.tenant_id == self._work.scope.tenant_id,
                    checkpoints.c.installation_id == self._work.scope.installation_id,
                    checkpoints.c.thread_id == thread,
                )).mappings().one_or_none()
                if state is not None:
                    # A concurrent claimant may commit between selection and this read.
                    if state["phase"] != "delivery_claimed" or state["deadline"] > now:
                        continue
                    transition_timeline_thread(connection, self._work.scope, thread_id=thread,
                        expected_revision=state["revision"], checkpoint_id="checkpoint:"+thread+":ambiguous",
                        phase="delivery_ambiguous", status="needs_help", event_type="DELEGATED_DELIVERY_AMBIGUOUS",
                        occurred_at=now, data={"error_code": "claim_expired"})
                    continue
                authorized = AuthorizedWorkDelivery(destination, work.spec.work_id, work.spec.sha256,
                    canonical_sha256(result.to_payload()), result.output,
                    result.state if isinstance(result, WorkFailureNotice) else "completed")
                claim = PrivateWorkDeliveryClaim(work.spec.work_id, UUID(work.spec.owner_principal_id),
                    UUID(work.spec.principal_binding_id), str(uuid4()), authorized.operation_id,
                    now + timedelta(seconds=lease_seconds))
                insert_initial_timeline_thread(connection, self._work.scope, thread_id=thread,
                    checkpoint_id="checkpoint:"+thread+":claimed", occurred_at=now,
                    data=claim.to_payload(), event_type="DELEGATED_DELIVERY_CLAIMED",
                    phase="delivery_claimed", status="active", deadline=claim.deadline,
                    operation_id=claim.operation_id)
                return claim
        return None

    def _document_authorized(self, work_id, owner, binding):
        with self._work._engine.begin() as connection:
            self._work._scope(connection)
            self._work._validate_owner_binding(connection, owner_principal_id=owner,
                                              principal_binding_id=binding)
        result = self._documents.load(work_id)
        if result is None:
            return None
        route = result.binding
        if (route['owner_principal_id'] != str(owner)
                or route['principal_binding_id'] != str(binding)):
            raise ValueError('document completion owner differs')
        if route['channel'] != 'telegram_private':
            return None
        destination = PrivateWorkDestination.from_payload(route['destination'])
        destination.validate_current(self._bindings_loader())
        recipient = PostgresPrivateTelegramRecipient(self._work._engine, self._work.scope,
            owner=owner, binding=binding, bindings=self._bindings_loader)
        if recipient.current_chat_id(now=self._clock()) != destination.chat_id:
            raise ValueError('document completion recipient changed')
        return AuthorizedWorkDelivery(destination, work_id, result.definition_sha256,
                                      result.result_sha256, result.output)

    def _claim_document_next(self, *, now, lease_seconds):
        if self._documents is None:
            return None
        for work_id in self._documents.candidates():
            # A stopped/revoked document cannot suppress unrelated eligible work.
            try:
                result = self._documents.load(work_id)
                if result is None or result.binding['channel'] != 'telegram_private':
                    continue
                owner = UUID(result.binding['owner_principal_id'])
                binding = UUID(result.binding['principal_binding_id'])
                authorized = self._document_authorized(work_id, owner, binding)
            except (ValueError, RuntimeError):
                continue
            if authorized is None:
                continue
            thread = self._delivery_thread(work_id)
            with self._work._engine.begin() as connection:
                self._work._scope(connection)
                connection.execute(sa.text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                                   {'key': thread})
                state = connection.execute(sa.select(operational_checkpoints).where(
                    operational_checkpoints.c.tenant_id == self._work.scope.tenant_id,
                    operational_checkpoints.c.installation_id == self._work.scope.installation_id,
                    operational_checkpoints.c.thread_id == thread,
                ).with_for_update()).mappings().one_or_none()
                if state is not None:
                    if state['phase'] == 'delivery_claimed' and state['deadline'] <= now:
                        transition_timeline_thread(connection, self._work.scope, thread_id=thread,
                            expected_revision=state['revision'], checkpoint_id='checkpoint:'+thread+':ambiguous',
                            phase='delivery_ambiguous', status='needs_help', event_type='DELEGATED_DELIVERY_AMBIGUOUS',
                            occurred_at=now, data={'error_code': 'claim_expired'})
                    continue
                if self._document_authorized(work_id, owner, binding) != authorized:
                    raise ValueError('document delivery authority changed before claim')
                claim = PrivateWorkDeliveryClaim(work_id, owner, binding, str(uuid4()),
                    authorized.operation_id, now + timedelta(seconds=lease_seconds))
                insert_initial_timeline_thread(connection, self._work.scope, thread_id=thread,
                    checkpoint_id='checkpoint:'+thread+':claimed', occurred_at=now,
                    data=claim.to_payload(), event_type='DELEGATED_DELIVERY_CLAIMED',
                    phase='delivery_claimed', status='active', deadline=claim.deadline,
                    operation_id=claim.operation_id)
                return claim
        return None

    def _finish(self, claim: PrivateWorkDeliveryClaim, *, now: datetime,
                receipt: str | None, error_code: str | None) -> str:
        require_utc_second(now, "delivery finish time")
        if receipt is not None and (not isinstance(receipt, str) or not receipt.strip() or len(receipt) > 256):
            raise ValueError("delivery receipt is invalid")
        if error_code is not None and (not isinstance(error_code, str)
                or re.fullmatch(r"[A-Za-z0-9_:-]{1,80}", error_code) is None):
            raise ValueError("delivery error code is invalid")
        if (receipt is None) == (error_code is None):
            raise ValueError("delivery finish requires one disposition")
        thread = self._delivery_thread(claim.work_id)
        with self._work._engine.begin() as connection:
            self._work._scope(connection)
            state = connection.execute(sa.select(operational_checkpoints).where(
                operational_checkpoints.c.tenant_id == self._work.scope.tenant_id,
                operational_checkpoints.c.installation_id == self._work.scope.installation_id,
                operational_checkpoints.c.thread_id == thread,
            ).with_for_update()).mappings().one_or_none()
            events = connection.execute(sa.select(operational_events).where(
                operational_events.c.tenant_id == self._work.scope.tenant_id,
                operational_events.c.installation_id == self._work.scope.installation_id,
                operational_events.c.thread_id == thread,
            ).order_by(operational_events.c.sequence)).mappings().all()
            if (state is None or not events or events[0]["payload"].get("data") != claim.to_payload()
                    or events[0]["event_type"] != "DELEGATED_DELIVERY_CLAIMED"):
                raise ValueError("delivery claim differs from durable authority")
            for event in events:
                if canonical_sha256(event["payload"]) != bytes(event["payload_sha256"]).hex():
                    raise ValueError("delivery event integrity differs")
            if now < events[0]["occurred_at"]:
                raise ValueError("delivery finish predates its claim")
            data = {"receipt": receipt} if receipt is not None else {"error_code": error_code}
            phase = "delivery_acknowledged" if receipt is not None else "delivery_ambiguous"
            if state["phase"] != "delivery_claimed":
                if state["phase"] == phase and events[-1]["payload"].get("data") == data:
                    return "already_recorded"
                raise ValueError("delivery already has a different terminal disposition")
            if now >= claim.deadline:
                phase, data = "delivery_ambiguous", {"error_code": "claim_expired"}
            transition_timeline_thread(connection, self._work.scope, thread_id=thread,
                expected_revision=claim.revision, checkpoint_id="checkpoint:"+thread+":terminal",
                phase=phase, status="completed" if phase == "delivery_acknowledged" else "needs_help",
                event_type="DELEGATED_DELIVERY_ACKNOWLEDGED" if phase == "delivery_acknowledged" else "DELEGATED_DELIVERY_AMBIGUOUS",
                occurred_at=now, data=data, operation_id=claim.operation_id)
            return "acknowledged" if phase == "delivery_acknowledged" else "ambiguous"

    def acknowledge(self, claim: PrivateWorkDeliveryClaim, receipt: str, *, now: datetime) -> str:
        return self._finish(claim, now=now, receipt=receipt, error_code=None)

    def mark_ambiguous(self, claim: PrivateWorkDeliveryClaim, error_code: str, *, now: datetime) -> str:
        return self._finish(claim, now=now, receipt=None, error_code=error_code)

    def status(self, work_id: str, *, owner_principal_id: UUID, principal_binding_id: UUID) -> str:
        work, destination = self._current(work_id, owner_principal_id=owner_principal_id,
                                          principal_binding_id=principal_binding_id)
        if destination is None:
            return "unbound"
        with self._work._engine.begin() as connection:
            self._work._scope(connection)
            phase = connection.scalar(sa.select(operational_checkpoints.c.phase).where(
                operational_checkpoints.c.tenant_id == self._work.scope.tenant_id,
                operational_checkpoints.c.installation_id == self._work.scope.installation_id,
                operational_checkpoints.c.thread_id == self._delivery_thread(work.spec.work_id)))
        return phase or "awaiting_result" if work.result is None else phase or "ready"
