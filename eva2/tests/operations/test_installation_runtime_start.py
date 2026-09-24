"""Controlled start boundary tests; never start a production container."""
from copy import deepcopy
from contextlib import nullcontext

import pytest

from tools import installation_runtime_start as subject
from test_restore_runtime_guard import fixture, Connection


class Engine:
    def __init__(self, expected):
        self.expected = expected
    def connect(self):
        return nullcontext(Connection(self.expected["target_database"]))


def setup(monkeypatch):
    expected, rows = fixture()
    worker = deepcopy(rows[-1]); worker.update(Name="/target-worker", Id="3"*64)
    rows.append(worker)
    expected["target"]["workers"].insert(0, {"name": "target-worker", "id": "3"*64, "image": worker["Image"]})
    calls = []
    def start(identifier):
        calls.append(identifier)
        row = next(row for row in rows if row["Id"] == identifier)
        row["State"].update(Running=True, Pid=100, Status="running", Health={"Status": "healthy"})
    monkeypatch.setattr(subject, "_released", lambda *args: None)
    args = dict(engine=Engine(expected), expected=expected, restore_id="restore", archive_sha256="a"*64,
                release_receipt={}, runtime_identity={}, inspect_all=lambda: rows, start_container=start)
    return args, rows, calls, start


def test_core_starts_first_and_only_pinned_targets(monkeypatch):
    args, rows, calls, _ = setup(monkeypatch)
    result = subject.start_restored_runtime(**args)
    assert calls == ["2"*64, "3"*64]
    assert result["status"] == "runtime_started_verified"
    assert [pin["id"] for pin in result["ready"]] == calls
    assert rows[0]["State"]["Running"] is False


@pytest.mark.parametrize("failure", ["source_running", "target_running", "identity", "release"])
def test_no_start_on_failed_preflight(monkeypatch, failure):
    args, rows, calls, _ = setup(monkeypatch)
    if failure == "release":
        monkeypatch.setattr(subject, "_released", lambda *args: (_ for _ in ()).throw(ValueError("held")))
    elif failure == "identity":
        rows[-1]["Id"] = "4"*64
    else:
        rows[0 if failure == "source_running" else 1]["State"]["Running"] = True
    with pytest.raises(subject.RestoredRuntimeStartError):
        subject.start_restored_runtime(**args)
    assert calls == []


@pytest.mark.parametrize("health", [None, "unhealthy", "starting"])
def test_missing_failed_or_timed_out_health_retains_partial_start(monkeypatch, health):
    args, rows, calls, start = setup(monkeypatch)
    def unhealthy(identifier):
        start(identifier)
        rows[1]["State"]["Health"] = {"Status": health}
    args.update(start_container=unhealthy, timeout_seconds=.001)
    with pytest.raises(subject.RestoredRuntimeStartError) as error:
        subject.start_restored_runtime(**args)
    assert calls == ["2"*64]
    assert error.value.receipt["started"][0]["id"] == calls[0]
    assert not error.value.receipt["ready"]
    assert rows[1]["State"]["Running"] is True


def test_unknown_start_outcome_is_observed_once_without_retry(monkeypatch):
    args, rows, calls, start = setup(monkeypatch)
    def lost_response(identifier):
        start(identifier)
        raise RuntimeError("lost command reply")
    args["start_container"] = lost_response
    with pytest.raises(subject.RestoredRuntimeStartError) as error:
        subject.start_restored_runtime(**args)
    assert calls == ["2"*64]
    assert error.value.receipt["attempted"] == error.value.receipt["started"]
    assert error.value.receipt["runtime_started"] is None


@pytest.mark.parametrize("change", ["source", "target_pin", "extra_writer"])
def test_fencing_and_identity_rechecked_after_first_start(monkeypatch, change):
    args, rows, calls, start = setup(monkeypatch)
    def changed(identifier):
        start(identifier)
        if change == "source":
            rows[0]["State"]["Running"] = True
        elif change == "target_pin":
            rows[-1]["Image"] = "sha256:" + "b"*64
        else:
            extra = deepcopy(rows[1]); extra.update(Name="/unexpected", Id="4"*64)
            rows.append(extra)
    args["start_container"] = changed
    with pytest.raises(subject.RestoredRuntimeStartError):
        subject.start_restored_runtime(**args)
    assert calls == ["2"*64]


class ReleasedConnection(Connection):
    def __init__(self, database, row):
        super().__init__(database); self.row = row; self.selected = None
    def execute(self, statement):
        self.selected = self.row; return self
    def exec_driver_sql(self, sql):
        self.selected = self.database; return self
    def one(self):
        return self.selected


@pytest.mark.parametrize("field", [None, "held", "archive_sha256", "restore_id", "release_authority_sha256", "runtime_tenant_id", "lineage_matches"])
def test_actual_committed_release_binding(monkeypatch, field):
    expected, _ = fixture()
    identity = {"tenant_id": "tenant", "installation_id": "installation"}
    row = dict(held=False, archive_sha256="a"*64, restore_id="restore", release_authority_sha256="b"*64,
               runtime_tenant_id="tenant", runtime_installation_id="installation", lineage_matches=True)
    receipt = dict(contract="EvaRestoreActivationRelease/v1", status="released_pending_commit", runtime_started=False,
                   restore_id="restore", archive_sha256="a"*64, release_authority_sha256="b"*64, target_database_oid=12345)
    if field:
        row[field] = True if field == "held" else False if field == "lineage_matches" else "other"
    engine = type("ReleasedEngine", (), {"connect": lambda self: nullcontext(ReleasedConnection(expected["target_database"], row))})()
    checked = []
    monkeypatch.setattr(subject, "require_restore_activation", lambda engine: checked.append(True))
    if field:
        with pytest.raises(ValueError):
            subject._released(engine, expected, "restore", "a"*64, receipt, identity)
    else:
        subject._released(engine, expected, "restore", "a"*64, receipt, identity)
    assert checked == [True]
