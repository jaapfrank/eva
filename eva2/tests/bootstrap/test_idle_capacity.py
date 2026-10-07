from types import SimpleNamespace

from eva.bootstrap.idle_capacity import idle_capacity_available, spare_compute_available


def test_container_quota_limits_visible_compute(monkeypatch):
    monkeypatch.setattr("eva.bootstrap.idle_capacity.os.sched_getaffinity", lambda _: set(range(8)))
    monkeypatch.setattr("eva.bootstrap.idle_capacity.Path.exists", lambda _: True)
    monkeypatch.setattr("eva.bootstrap.idle_capacity.Path.read_text", lambda _: "50000 100000")
    monkeypatch.setattr("eva.bootstrap.idle_capacity.os.getloadavg", lambda: (0.7, 0, 0))
    assert spare_compute_available() is False
    monkeypatch.setattr("eva.bootstrap.idle_capacity.os.getloadavg", lambda: (0.2, 0, 0))
    assert spare_compute_available() is True


def test_active_work_or_exhausted_budget_starts_no_optional_work(monkeypatch):
    compute_checks = []
    monkeypatch.setattr("eva.bootstrap.idle_capacity.spare_compute_available",
                        lambda: compute_checks.append(True) or True)
    model = SimpleNamespace(autonomous_admission_blocker=lambda: None)
    budget = SimpleNamespace(has_installation_capacity=lambda **_: False)
    store = SimpleNamespace(has_pending_or_running_work=lambda: True)
    assert not idle_capacity_available(model=model, reflection_budget=budget, delegated_work_store=store)
    budget.has_installation_capacity = lambda **_: True
    assert not idle_capacity_available(model=model, reflection_budget=budget, delegated_work_store=store)
    assert compute_checks == []
    store.has_pending_or_running_work = lambda: False
    assert idle_capacity_available(model=model, reflection_budget=budget, delegated_work_store=store)
    assert compute_checks == [True]


def test_shared_respond_marks_foreground_until_early_return_and_exception():
    import pytest
    from eva.application.memory_background import HindsightColdPathWorker
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    runtime._cold_path = HindsightColdPathWorker(SimpleNamespace())
    def reply(turn, recent):
        assert runtime._cold_path._foreground_count == 1
        return "cached"
    runtime._respond_admitted = reply
    assert runtime.respond(object()) == "cached"
    assert runtime._cold_path._foreground_count == 0
    def fail(turn, recent):
        raise ValueError("provider failed")
    runtime._respond_admitted = fail
    with pytest.raises(ValueError):
        runtime.respond(object())
    assert runtime._cold_path._foreground_count == 0

def test_capacity_peek_uses_actual_whole_second_budget_contract(monkeypatch):
    from eva.adapters.postgres.runtime_resource_usage import PostgresDailyReflectionBudget
    from eva.adapters.postgres.operational_timeline import OperationalTimelineError
    class Timeline:
        def load(self, thread_id):
            raise OperationalTimelineError("thread does not exist")
    budget = object.__new__(PostgresDailyReflectionBudget)
    budget._timeline, budget._installation_limit = Timeline(), 2
    monkeypatch.setattr("eva.bootstrap.idle_capacity.spare_compute_available", lambda: True)
    assert idle_capacity_available(
        model=SimpleNamespace(autonomous_admission_blocker=lambda: None),
        reflection_budget=budget, delegated_work_store=None)

def test_scoped_active_work_read_keeps_stopping_call_busy_until_terminal(monkeypatch):
    import sqlalchemy as sa
    import eva.adapters.postgres.delegated_work as adapter
    table = sa.Table("activity", sa.MetaData(),
        sa.Column("tenant_id", sa.String), sa.Column("installation_id", sa.String),
        sa.Column("state", sa.String), sa.Column("stop_requested_at", sa.String))
    engine = sa.create_engine("sqlite://")
    table.metadata.create_all(engine)
    monkeypatch.setattr(adapter, "delegated_work_runs", table)
    store = object.__new__(adapter.PostgresDelegatedWorkStore)
    store._engine = engine
    store._scope_value = SimpleNamespace(tenant_id="tenant", installation_id="installation")
    store._scope = lambda connection: None
    with engine.begin() as connection:
        connection.execute(table.insert(), [
            dict(tenant_id="other", installation_id="installation",
                 state="call_started", stop_requested_at=None),
            dict(tenant_id="tenant", installation_id="installation",
                 state="admitted", stop_requested_at="requested")])
    assert store.has_pending_or_running_work() is False
    with engine.begin() as connection:
        connection.execute(table.insert(), dict(tenant_id="tenant", installation_id="installation",
            state="call_started", stop_requested_at="requested"))
    assert store.has_pending_or_running_work() is True
    with engine.begin() as connection:
        connection.execute(table.update().where(table.c.tenant_id == "tenant",
            table.c.state == "call_started").values(state="stopped"))
    assert store.has_pending_or_running_work() is False
