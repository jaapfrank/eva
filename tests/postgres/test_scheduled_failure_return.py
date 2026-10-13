"""Real PostgreSQL scheduled failures; no model, transport send or task execution."""
from dataclasses import replace
from datetime import timedelta
import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from eva.operations.install_schema import install_schema
from eva.adapters.postgres.scheduled_work import ScheduledOwnerWork
from eva.adapters.postgres.current_catalog import delegated_work_runs
from tests.postgres.test_delegated_work_store import principal_bindings
from eva.adapters.postgres.scheduled_work_failure import ScheduledFailureSource
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.application.conversation import ConversationTurn
from tests.postgres.test_private_work_return import setup as private_setup, start
from tests.postgres.test_delegated_delivery_store import setup as telegram_setup, NOW


@pytest.fixture(scope="module")
def database():
    dsn = os.environ.get("EVA2_IN002_TEST_ADMIN_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL admin DSN required")
    url = make_url(dsn)
    name = "eva2_4b_disposable_schedule_" + uuid4().hex
    admin = sa.create_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    engine = None
    with admin.connect() as connection:
        connection.exec_driver_sql('CREATE DATABASE "' + name + '"')
    try:
        target = url.set(database=name)
        install_schema(target.render_as_string(hide_password=False),
                       alembic_ini=Path(__file__).resolve().parents[2] / "alembic.ini")
        engine = sa.create_engine(target, poolclass=NullPool)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql('DROP DATABASE "' + name + '" WITH (FORCE)')
        admin.dispose()


def block_schedule(service, turn, at, monkeypatch, *, predecessor=False):
    scheduler = ScheduledOwnerWork(service)
    service.digital_profile = object()  # Admission availability only; execution is never invoked.
    if predecessor:
        work = start(service, turn)
        request = {"task": "Summarize the saved result", "after_work_id": work.work_id}
    else:
        request = {"task": "Save tomorrow's report", "run_at": (at[0] + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")}
    scheduled = scheduler.schedule(turn, request_key="schedule", request=request)
    if predecessor:
        service.store.request_stop(work.work_id, **service._owner, now=at[0])
    else:
        def unavailable(*args, **kwargs):
            raise ValueError("synthetic admission failure")
        monkeypatch.setattr(service, "_execute", unavailable)
    with service.store._engine.connect() as connection:
        before = connection.scalar(sa.select(sa.func.count()).select_from(delegated_work_runs))
    at[0] += timedelta(seconds=2)
    assert scheduler.run_once() == "blocked"
    with service.store._engine.connect() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(delegated_work_runs)) == before
    return scheduled["schedule_id"]


@pytest.mark.parametrize("predecessor", [False, True])
def test_private_failure_retries_transfer_then_acknowledges_without_new_work(database, monkeypatch, predecessor):
    service, turn, at = private_setup(database, "scheduled-private-" + str(predecessor), False)
    schedule_id = block_schedule(service, turn, at, monkeypatch, predecessor=predecessor)
    result = service.private_return(turn, "poll")["result"]
    assert result["work_id"] == schedule_id and result["outcome"] == "needs_help"
    assert "niet automatisch opnieuw gestart" in result["output"]
    assert service.private_return(turn, "poll")["result"] == result
    identity = {key: result[key] for key in ("work_id", "operation_id")}
    assert service.private_return(turn, "authorize", **identity)["output_sha256"] == result["output_sha256"]
    assert service.private_return(turn, "ack", **identity)["status"] == "acknowledged"
    assert service.private_return(turn, "poll")["result"] is None
    with database.begin() as connection:
        connection.execute(sa.update(principal_bindings).where(
            principal_bindings.c.principal_binding_id == service.principal_binding_id).values(status="revoked"))
    with pytest.raises((ValueError, RuntimeError)):
        ScheduledFailureSource(service.store).load(schedule_id)


@pytest.mark.parametrize("ambiguous", [False, True])
def test_telegram_failure_uses_original_recipient_and_existing_send_fence(database, monkeypatch, ambiguous):
    label = "scheduled-telegram-" + str(ambiguous)
    work, spec, destination, delivery, bindings, owner = telegram_setup(database, label)
    at = [NOW]
    service = DelegatedWorkOwnerService(work, **owner, clock=lambda: at[0],
        private_bridge_principal_loader=lambda: "telegram:42",
        delivery_destination_loader=lambda turn: destination)
    turn = ConversationTurn.private("tenant:" + label, "installation:" + label, "telegram:42",
        "telegram:42", "telegram:42:1", "Save a report later", NOW.isoformat().replace("+00:00", "Z"))
    schedule_id = block_schedule(service, turn, at, monkeypatch)
    claim = delivery.claim_next(now=at[0])
    assert claim.work_id == schedule_id
    result = delivery.authorize_delivery(schedule_id, **owner)
    assert result.destination == destination and result.outcome == "needs_help"
    if ambiguous:
        assert delivery.mark_ambiguous(claim, "uncertain_send", now=at[0]) == "ambiguous"
    else:
        assert delivery.acknowledge(claim, "42:9", now=at[0]) == "acknowledged"
    assert delivery.claim_next(now=at[0] + timedelta(seconds=125)) is None
    with database.begin() as connection:
        connection.execute(sa.update(principal_bindings).where(
            principal_bindings.c.principal_binding_id == owner["principal_binding_id"]).values(status="revoked"))
    with pytest.raises((ValueError, RuntimeError)):
        delivery.authorize_delivery(schedule_id, **owner)


def test_historical_schedule_replay_never_backfills_private_delivery(database, monkeypatch):
    from eva.adapters.postgres import scheduled_work as module
    service, turn, at = private_setup(database, "scheduled-historical", False)
    service.digital_profile = object()
    scheduler = ScheduledOwnerWork(service)
    request = {"task": "Old scheduled report", "run_at": (at[0] + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")}
    original = module.insert_initial_timeline_thread
    def historical_insert(*args, **kwargs):
        kwargs["data"] = {key: value for key, value in kwargs["data"].items() if key != "return_binding"}
        return original(*args, **kwargs)
    with monkeypatch.context() as historical:
        historical.setattr(module, "insert_initial_timeline_thread", historical_insert)
        saved = scheduler.schedule(turn, request_key="old", request=request)
    assert scheduler.schedule(turn, request_key="old", request=request) == saved
    monkeypatch.setattr(service, "_execute", lambda *a, **k: (_ for _ in ()).throw(ValueError("unavailable")))
    at[0] += timedelta(seconds=2)
    assert scheduler.run_once() == "blocked"
    assert ScheduledFailureSource(service.store).load(saved["schedule_id"]) is None
    assert service.private_return(turn, "poll")["result"] is None
