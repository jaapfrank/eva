from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.engine import make_url

from conftest import Postgres4CGate, Seeded4CEpisode
from eva.adapters.dbos.datasource import open_runtime_datasource
from eva.bootstrap.episode_transition_4c import configure_episode_transition_runtime
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.contracts.lifecycle import EpisodeState


WORKER = Path(__file__).with_name("fault_worker.py")
TCP_PROXY = Path(__file__).with_name("tcp_commit_ack_proxy.py")
ROUTE_CUT_PROXY = Path(__file__).with_name("tcp_route_cut_proxy.py")
TIMEOUT_SECONDS = 10.0


@dataclass(slots=True)
class WorkerProcess:
    process: subprocess.Popen[str]
    markers: Path
    spec: Path


def _workflow_id(label: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"eva2-4c-workflow-{label}"))


def _start_worker(
    tmp_path: Path,
    gate: Postgres4CGate,
    seed: Seeded4CEpisode,
    *,
    label: str,
    mode: str = "normal",
    action: str = "start",
    datasource_dsn: str | None = None,
    workflow_id: str | None = None,
    executor_id: str | None = None,
    fault_attempts: int = 0,
) -> WorkerProcess:
    root = tmp_path / label
    markers = root / "markers"
    markers.mkdir(parents=True)
    spec_path = root / "spec.json"
    spec = {
        "system_dsn": gate.runtime_dsn,
        "datasource_dsn": datasource_dsn or gate.runtime_dsn,
        "workflow_id": workflow_id or _workflow_id(label),
        "payload": asdict(seed.command),
        "mode": mode,
        "action": action,
        "markers": str(markers),
        "application_version": "4c-gate-v1",
        "executor_id": executor_id or f"4c-{label}",
        "fault_attempts": fault_attempts,
    }
    spec_path.write_text(json.dumps(spec, sort_keys=True), encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(WORKER), str(spec_path)],
        cwd=Path(__file__).resolve().parents[3],
        env=os.environ.copy(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return WorkerProcess(process=process, markers=markers, spec=spec_path)


def _wait_marker(worker: WorkerProcess, name: str) -> Path:
    marker = worker.markers / name
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if marker.exists():
            return marker
        if worker.process.poll() is not None:
            stdout, stderr = worker.process.communicate()
            pytest.fail(
                f"worker exited before {name}: rc={worker.process.returncode}\n"
                f"stdout={stdout}\nstderr={stderr}"
            )
        time.sleep(0.01)
    worker.process.kill()
    stdout, stderr = worker.process.communicate()
    pytest.fail(f"worker did not reach {name} in 10s\n{stdout}\n{stderr}")


def _finish(worker: WorkerProcess, expected_returncode: int = 0) -> tuple[str, str]:
    try:
        stdout, stderr = worker.process.communicate(timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        worker.process.kill()
        stdout, stderr = worker.process.communicate()
        pytest.fail(f"worker exceeded 10s\nstdout={stdout}\nstderr={stderr}")
    assert worker.process.returncode == expected_returncode, (
        f"worker rc={worker.process.returncode}\nstdout={stdout}\nstderr={stderr}\n"
        f"error={(worker.markers / 'error').read_text() if (worker.markers / 'error').exists() else ''}"
    )
    return stdout, stderr


def _wait_file(path: Path, process: subprocess.Popen[str]) -> Path:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if path.exists():
            return path
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(
                f"support process exited before {path.name}: "
                f"rc={process.returncode}\n{stdout}\n{stderr}"
            )
        time.sleep(0.01)
    process.kill()
    stdout, stderr = process.communicate()
    pytest.fail(f"support process timeout for {path}\n{stdout}\n{stderr}")


def _head_result(row: sa.RowMapping) -> dict[str, object]:
    updated_at: datetime = row["updated_at"]
    state = EpisodeState.model_validate(
        {
            "schema_version": row["state_schema_version"],
            "contract": row["state_contract"],
            "episode_id": str(row["episode_id"]),
            "tenant_id": str(row["tenant_id"]),
            "principal_id": str(row["principal_id"]),
            "conversation_id": str(row["conversation_id"]),
            "ingress_request_id": str(row["ingress_request_id"]),
            "state": row["state"],
            "current_generation": row["current_generation"],
            "supersedes_generation": row["supersedes_generation"],
            "last_event_id": str(row["last_event_id"]),
            "updated_at": updated_at.astimezone(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }
    )
    assert bytes(row["state_payload_sha256"]).hex() == canonical_sha256(state)
    return {"revision": row["revision"], "state": state.model_dump(mode="json")}


def _assert_atomic_result(
    gate: Postgres4CGate,
    seed: Seeded4CEpisode,
    workflow_id: str,
) -> tuple[str, str, str]:
    with gate.admin_engine.connect() as connection:
        head = (
            connection.execute(
                sa.text(
                    "SELECT *, xmin::text AS row_xmin FROM eva_core.episodes "
                    "WHERE tenant_id=:tenant AND installation_id=:installation "
                    "AND episode_id=:episode"
                ),
                {
                    "tenant": seed.tenant_id,
                    "installation": seed.installation_id,
                    "episode": seed.episode_id,
                },
            )
            .mappings()
            .one()
        )
        event_row = (
            connection.execute(
                sa.text(
                    "SELECT after_revision, after_episode_state, "
                    "after_state_sha256, xmin::text AS row_xmin "
                    "FROM eva_core.episode_events WHERE tenant_id=:tenant "
                    "AND installation_id=:installation AND command_id=:command"
                ),
                {
                    "tenant": seed.tenant_id,
                    "installation": seed.installation_id,
                    "command": seed.command.command_id,
                },
            )
            .mappings()
            .one()
        )
        durable = (
            connection.execute(
                sa.text(
                    "SELECT output, error, serialization, xmin::text AS row_xmin "
                    "FROM dbos.datasource_outputs WHERE workflow_id=:workflow"
                ),
                {"workflow": workflow_id},
            )
            .mappings()
            .one()
        )

    head_result = _head_result(head)
    event_state = EpisodeState.model_validate(event_row["after_episode_state"])
    assert bytes(event_row["after_state_sha256"]).hex() == canonical_sha256(event_state)
    event_result = {
        "revision": event_row["after_revision"],
        "state": event_state.model_dump(mode="json"),
    }
    expected_bytes = canonical_json_bytes(seed.expected_result)
    assert canonical_json_bytes(head_result) == expected_bytes
    assert canonical_json_bytes(event_result) == expected_bytes
    assert durable["error"] is None
    assert durable["serialization"] == "portable_json"
    assert durable["output"].encode("utf-8") == expected_bytes
    assert head["row_xmin"] == event_row["row_xmin"] == durable["row_xmin"]
    return head["row_xmin"], event_row["row_xmin"], durable["row_xmin"]


def _transition_counts(gate: Postgres4CGate, seed: Seeded4CEpisode) -> tuple[int, int]:
    with gate.admin_engine.connect() as connection:
        revision = connection.scalar(
            sa.text(
                "SELECT revision FROM eva_core.episodes WHERE tenant_id=:tenant "
                "AND installation_id=:installation AND episode_id=:episode"
            ),
            {
                "tenant": seed.tenant_id,
                "installation": seed.installation_id,
                "episode": seed.episode_id,
            },
        )
        events = connection.scalar(
            sa.text(
                "SELECT count(*) FROM eva_core.episode_events "
                "WHERE tenant_id=:tenant AND installation_id=:installation "
                "AND episode_id=:episode AND event_kind='state_transition'"
            ),
            {
                "tenant": seed.tenant_id,
                "installation": seed.installation_id,
                "episode": seed.episode_id,
            },
        )
    return int(revision), int(events)


def test_normal_transition_has_one_physical_commit_and_byte_exact_result(
    postgres_4c_gate: Postgres4CGate,
    seed_4c_episode: object,
    tmp_path: Path,
) -> None:
    seed = seed_4c_episode("normal")  # type: ignore[operator]
    workflow_id = _workflow_id("normal")
    worker = _start_worker(tmp_path, postgres_4c_gate, seed, label="normal")

    _finish(worker)

    assert (worker.markers / "result.json").read_bytes() == canonical_json_bytes(
        seed.expected_result
    )
    _assert_atomic_result(postgres_4c_gate, seed, workflow_id)
    assert _transition_counts(postgres_4c_gate, seed) == (2, 1)


def test_start_detaches_payload_before_dbos_executor_dispatch(
    postgres_4c_gate: Postgres4CGate,
    seed_4c_episode: object,
) -> None:
    seed = seed_4c_episode("detached-payload")  # type: ignore[operator]
    workflow_id = _workflow_id("detached-payload")
    payload = asdict(seed.command)
    datasource = open_runtime_datasource(postgres_4c_gate.runtime_dsn)
    raw_datasource = object.__getattribute__(
        datasource, "_EvaDBOSDatasource__datasource"
    )
    reached = threading.Event()
    release = threading.Event()
    result: list[dict[str, object]] = []
    errors: list[BaseException] = []

    @event.listens_for(raw_datasource.engine, "before_cursor_execute")
    def block_before_body(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        normalized = " ".join(statement.lower().split())
        if (
            "from dbos.datasource_outputs" in normalized
            and "workflow_id" in normalized
            and "step_id" in normalized
            and not reached.is_set()
        ):
            reached.set()
            if not release.wait(TIMEOUT_SECONDS):
                raise TimeoutError("payload mutation barrier was not released")

    runtime = configure_episode_transition_runtime(
        datasource,
        system_database_url=postgres_4c_gate.runtime_dsn,
        application_version="4c-gate-v1",
        executor_id="4c-detached-payload",
    )
    runtime.launch()

    def invoke() -> None:
        try:
            result.append(runtime.start(workflow_id, payload))
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=invoke)
    try:
        thread.start()
        assert reached.wait(TIMEOUT_SECONDS)
        payload["expected_revision"] = 999
        release.set()
        thread.join(TIMEOUT_SECONDS)
        assert not thread.is_alive()
        assert errors == []
        assert result == [seed.expected_result]
        assert payload["expected_revision"] == 999
        _assert_atomic_result(postgres_4c_gate, seed, workflow_id)
    finally:
        release.set()
        thread.join(TIMEOUT_SECONDS)
        runtime.close()


def test_body_sentinel_rolls_back_eva_mutation_before_error_checkpoint(
    postgres_4c_gate: Postgres4CGate,
    seed_4c_episode: object,
    tmp_path: Path,
) -> None:
    seed = seed_4c_episode("sentinel")  # type: ignore[operator]
    workflow_id = _workflow_id("sentinel")
    worker = _start_worker(
        tmp_path,
        postgres_4c_gate,
        seed,
        label="sentinel",
        mode="sentinel_after_event",
    )

    _finish(worker, expected_returncode=70)

    assert (worker.markers / "reached").exists()
    assert (worker.markers / "fired").exists()
    assert _transition_counts(postgres_4c_gate, seed) == (1, 0)
    with postgres_4c_gate.admin_engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT output, error FROM dbos.datasource_outputs "
                "WHERE workflow_id=:workflow"
            ),
            {"workflow": workflow_id},
        ).one()
    assert row.output is None
    assert row.error is not None


def test_same_command_new_workflow_reaches_body_without_eva_mutation(
    postgres_4c_gate: Postgres4CGate,
    seed_4c_episode: object,
    tmp_path: Path,
) -> None:
    seed = seed_4c_episode("new-workflow-replay")  # type: ignore[operator]
    first_id = _workflow_id("new-workflow-replay-first")
    first = _start_worker(
        tmp_path,
        postgres_4c_gate,
        seed,
        label="new-workflow-replay-first",
        workflow_id=first_id,
    )
    _finish(first)
    _assert_atomic_result(postgres_4c_gate, seed, first_id)
    with postgres_4c_gate.admin_engine.connect() as connection:
        before = connection.execute(
            sa.text(
                "SELECT xmin::text FROM eva_core.episodes WHERE episode_id=:episode"
            ),
            {"episode": seed.episode_id},
        ).scalar_one()

    second_id = _workflow_id("new-workflow-replay-second")
    second = _start_worker(
        tmp_path,
        postgres_4c_gate,
        seed,
        label="new-workflow-replay-second",
        workflow_id=second_id,
        mode="body_marker",
    )
    _finish(second)

    assert (second.markers / "body").exists()
    assert (second.markers / "result.json").read_bytes() == canonical_json_bytes(
        seed.expected_result
    )
    assert _transition_counts(postgres_4c_gate, seed) == (2, 1)
    with postgres_4c_gate.admin_engine.connect() as connection:
        after = connection.execute(
            sa.text(
                "SELECT xmin::text FROM eva_core.episodes WHERE episode_id=:episode"
            ),
            {"episode": seed.episode_id},
        ).scalar_one()
        second_output = connection.scalar(
            sa.text(
                "SELECT output FROM dbos.datasource_outputs "
                "WHERE workflow_id=:workflow"
            ),
            {"workflow": second_id},
        )
    assert after == before
    assert second_output.encode("utf-8") == canonical_json_bytes(seed.expected_result)


def test_two_os_processes_converge_on_one_transition_and_identical_results(
    postgres_4c_gate: Postgres4CGate,
    seed_4c_episode: object,
    tmp_path: Path,
) -> None:
    seed = seed_4c_episode("concurrent")  # type: ignore[operator]
    ids = [_workflow_id("concurrent-one"), _workflow_id("concurrent-two")]
    workers = [
        _start_worker(
            tmp_path,
            postgres_4c_gate,
            seed,
            label=f"concurrent-{index}",
            workflow_id=ids[index],
            executor_id=f"4c-concurrent-{index}",
            mode="barrier_before_update",
        )
        for index in range(2)
    ]
    for worker in workers:
        _wait_marker(worker, "reached")
    for worker in workers:
        (worker.markers / "release").write_text("1\n", encoding="utf-8")
    for worker in workers:
        _finish(worker)

    assert all((worker.markers / "fired").exists() for worker in workers)
    assert _transition_counts(postgres_4c_gate, seed) == (2, 1)
    expected = canonical_json_bytes(seed.expected_result)
    assert all(
        (worker.markers / "result.json").read_bytes() == expected for worker in workers
    )
    with postgres_4c_gate.admin_engine.connect() as connection:
        head_xmin = connection.scalar(
            sa.text(
                "SELECT xmin::text FROM eva_core.episodes WHERE episode_id=:episode"
            ),
            {"episode": seed.episode_id},
        )
        event_xmin = connection.scalar(
            sa.text(
                "SELECT xmin::text FROM eva_core.episode_events "
                "WHERE command_id=:command"
            ),
            {"command": seed.command.command_id},
        )
        durable = connection.execute(
            sa.text(
                "SELECT output, xmin::text FROM dbos.datasource_outputs "
                "WHERE workflow_id IN (:first, :second) ORDER BY workflow_id"
            ),
            {"first": ids[0], "second": ids[1]},
        ).all()
    assert head_xmin == event_xmin
    assert sum(row[1] == head_xmin for row in durable) == 1
    assert [row[0].encode("utf-8") for row in durable] == [expected, expected]


def _install_body_update_counter(gate: Postgres4CGate) -> None:
    with gate.admin_engine.begin() as connection:
        connection.exec_driver_sql("DROP SCHEMA IF EXISTS eva_4c_fault CASCADE")
        connection.exec_driver_sql("CREATE SCHEMA eva_4c_fault")
        connection.exec_driver_sql("CREATE SEQUENCE eva_4c_fault.attempts")
        connection.exec_driver_sql(
            """
            CREATE FUNCTION eva_4c_fault.count_update() RETURNS trigger
            LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
            BEGIN
                PERFORM nextval('eva_4c_fault.attempts');
                RETURN NEW;
            END $$
            """
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER eva_4c_serialization BEFORE UPDATE ON "
            "eva_core.episodes FOR EACH ROW EXECUTE FUNCTION "
            "eva_4c_fault.count_update()"
        )


def _drop_body_update_counter(gate: Postgres4CGate) -> None:
    with gate.admin_engine.begin() as connection:
        connection.exec_driver_sql("DROP SCHEMA IF EXISTS eva_4c_fault CASCADE")


def _force_real_serialization_conflicts(
    gate: Postgres4CGate,
    seed: Seeded4CEpisode,
    worker: WorkerProcess,
    count: int,
) -> None:
    for attempt in range(1, count + 1):
        _wait_marker(worker, f"reached-{attempt}")
        with gate.admin_engine.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE eva_core.episodes SET updated_at=updated_at "
                    "WHERE tenant_id=:tenant AND installation_id=:installation "
                    "AND episode_id=:episode"
                ),
                {
                    "tenant": seed.tenant_id,
                    "installation": seed.installation_id,
                    "episode": seed.episode_id,
                },
            )
        (worker.markers / f"release-{attempt}").write_text("1\n", encoding="utf-8")
        _wait_marker(worker, f"fired-{attempt}")


def test_real_40001_succeeds_on_third_and_never_exceeds_three_attempts(
    postgres_4c_gate: Postgres4CGate,
    seed_4c_episode: object,
    tmp_path: Path,
) -> None:
    seed = seed_4c_episode("serialization-success")  # type: ignore[operator]
    workflow_id = _workflow_id("serialization-success")
    worker = _start_worker(
        tmp_path,
        postgres_4c_gate,
        seed,
        label="serialization-success",
        mode="real_40001",
        fault_attempts=2,
    )
    _force_real_serialization_conflicts(postgres_4c_gate, seed, worker, 2)
    _finish(worker)

    assert (worker.markers / "attempt-3").exists()
    assert not (worker.markers / "attempt-4").exists()
    assert (worker.markers / "sqlstates").read_text().splitlines() == [
        "40001",
        "40001",
    ]
    _assert_atomic_result(postgres_4c_gate, seed, workflow_id)


def test_real_40001_exhaustion_stops_at_three_and_checkpoints_no_eva_change(
    postgres_4c_gate: Postgres4CGate,
    seed_4c_episode: object,
    tmp_path: Path,
) -> None:
    seed = seed_4c_episode("serialization-exhaustion")  # type: ignore[operator]
    workflow_id = _workflow_id("serialization-exhaustion")
    worker = _start_worker(
        tmp_path,
        postgres_4c_gate,
        seed,
        label="serialization-exhaustion",
        mode="real_40001",
        fault_attempts=3,
    )
    _force_real_serialization_conflicts(postgres_4c_gate, seed, worker, 3)
    _finish(worker, expected_returncode=70)
    with postgres_4c_gate.admin_engine.connect() as connection:
        durable = connection.execute(
            sa.text(
                "SELECT output, error FROM dbos.datasource_outputs "
                "WHERE workflow_id=:workflow"
            ),
            {"workflow": workflow_id},
        ).one()
    assert (worker.markers / "attempt-3").exists()
    assert not (worker.markers / "attempt-4").exists()
    assert (worker.markers / "sqlstates").read_text().splitlines() == [
        "40001",
        "40001",
        "40001",
    ]
    assert durable.output is None and durable.error is not None
    assert _transition_counts(postgres_4c_gate, seed) == (1, 0)


def _install_deadlock_gate(gate: Postgres4CGate) -> None:
    with gate.admin_engine.begin() as connection:
        connection.exec_driver_sql("DROP SCHEMA IF EXISTS eva_4c_deadlock CASCADE")
        connection.exec_driver_sql("CREATE SCHEMA eva_4c_deadlock")
        connection.exec_driver_sql(
            "CREATE TABLE eva_4c_deadlock.locks "
            "(id integer PRIMARY KEY, value integer NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO eva_4c_deadlock.locks VALUES (1, 0), (2, 0)"
        )
        connection.exec_driver_sql("GRANT USAGE ON SCHEMA eva_4c_deadlock TO eva_dbos")
        connection.exec_driver_sql(
            "GRANT SELECT, UPDATE ON eva_4c_deadlock.locks TO eva_dbos"
        )


def _drop_deadlock_gate(gate: Postgres4CGate) -> None:
    with gate.admin_engine.begin() as connection:
        connection.exec_driver_sql("DROP SCHEMA IF EXISTS eva_4c_deadlock CASCADE")


def _force_real_deadlocks(
    gate: Postgres4CGate,
    worker: WorkerProcess,
    count: int,
) -> None:
    for attempt in range(1, count + 1):
        _wait_marker(worker, f"awaiting-{attempt}")
        with gate.admin_engine.connect() as blocker:
            transaction = blocker.begin()
            blocker.exec_driver_sql("SET LOCAL deadlock_timeout='5s'")
            blocker.exec_driver_sql(
                "SELECT id FROM eva_4c_deadlock.locks WHERE id=1 FOR UPDATE"
            )
            (worker.markers / f"blocker-ready-{attempt}").write_text(
                "1\n", encoding="utf-8"
            )
            _wait_marker(worker, f"reached-{attempt}")
            blocker.exec_driver_sql(
                "SELECT id FROM eva_4c_deadlock.locks WHERE id=2 FOR UPDATE"
            )
            transaction.commit()
        _wait_marker(worker, f"fired-{attempt}")


def test_real_40p01_succeeds_on_third_and_never_exceeds_three_attempts(
    postgres_4c_gate: Postgres4CGate,
    seed_4c_episode: object,
    tmp_path: Path,
) -> None:
    seed = seed_4c_episode("deadlock-success")  # type: ignore[operator]
    workflow_id = _workflow_id("deadlock-success")
    _install_deadlock_gate(postgres_4c_gate)
    try:
        worker = _start_worker(
            tmp_path,
            postgres_4c_gate,
            seed,
            label="deadlock-success",
            mode="real_40p01",
            fault_attempts=2,
        )
        _force_real_deadlocks(postgres_4c_gate, worker, 2)
        _finish(worker)
        assert (worker.markers / "attempt-3").exists()
        assert not (worker.markers / "attempt-4").exists()
        assert (worker.markers / "sqlstates").read_text().splitlines() == [
            "40P01",
            "40P01",
        ]
        _assert_atomic_result(postgres_4c_gate, seed, workflow_id)
    finally:
        _drop_deadlock_gate(postgres_4c_gate)


def test_real_40p01_exhaustion_stops_at_three_and_checkpoints_no_eva_change(
    postgres_4c_gate: Postgres4CGate,
    seed_4c_episode: object,
    tmp_path: Path,
) -> None:
    seed = seed_4c_episode("deadlock-exhaustion")  # type: ignore[operator]
    workflow_id = _workflow_id("deadlock-exhaustion")
    _install_deadlock_gate(postgres_4c_gate)
    try:
        worker = _start_worker(
            tmp_path,
            postgres_4c_gate,
            seed,
            label="deadlock-exhaustion",
            mode="real_40p01",
            fault_attempts=3,
        )
        _force_real_deadlocks(postgres_4c_gate, worker, 3)
        _finish(worker, expected_returncode=70)
        with postgres_4c_gate.admin_engine.connect() as connection:
            durable = connection.execute(
                sa.text(
                    "SELECT output, error FROM dbos.datasource_outputs "
                    "WHERE workflow_id=:workflow"
                ),
                {"workflow": workflow_id},
            ).one()
        assert (worker.markers / "attempt-3").exists()
        assert not (worker.markers / "attempt-4").exists()
        assert (worker.markers / "sqlstates").read_text().splitlines() == [
            "40P01",
            "40P01",
            "40P01",
        ]
        assert durable.output is None and durable.error is not None
        assert _transition_counts(postgres_4c_gate, seed) == (1, 0)
    finally:
        _drop_deadlock_gate(postgres_4c_gate)


def test_real_tcp_lost_commit_ack_reconciles_before_any_body_reexecution(
    postgres_4c_gate: Postgres4CGate,
    seed_4c_episode: object,
    tmp_path: Path,
) -> None:
    seed = seed_4c_episode("lost-commit-ack")  # type: ignore[operator]
    workflow_id = _workflow_id("lost-commit-ack")
    proxy_markers = tmp_path / "lost-ack-proxy"
    proxy_markers.mkdir()
    target = make_url(postgres_4c_gate.admin_dsn)
    proxy = subprocess.Popen(
        [
            sys.executable,
            str(TCP_PROXY),
            "--target-host",
            str(target.host),
            "--target-port",
            str(target.port),
            "--markers",
            str(proxy_markers),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    ready = json.loads(_wait_file(proxy_markers / "ready", proxy).read_text())
    proxy_url = (
        make_url(postgres_4c_gate.runtime_dsn)
        .set(host=ready["host"], port=ready["port"])
        .update_query_dict({"sslmode": "disable"})
    )
    (proxy_markers / "arm").write_text("1\n", encoding="utf-8")
    _install_body_update_counter(postgres_4c_gate)
    try:
        worker = _start_worker(
            tmp_path,
            postgres_4c_gate,
            seed,
            label="lost-commit-ack",
            workflow_id=workflow_id,
            datasource_dsn=proxy_url.render_as_string(hide_password=False),
            mode="body_count",
        )
        _finish(worker)
        assert _wait_file(proxy_markers / "reached", proxy).exists()
        assert _wait_file(proxy_markers / "fired", proxy).exists()
        assert _wait_file(proxy_markers / "reconciled", proxy).exists()
        with postgres_4c_gate.admin_engine.connect() as connection:
            body_attempts = connection.scalar(
                sa.text("SELECT last_value FROM eva_4c_fault.attempts")
            )
        assert body_attempts == 1
        assert (worker.markers / "body_entries").read_text().splitlines() == ["body"]
        _assert_atomic_result(postgres_4c_gate, seed, workflow_id)
    finally:
        _drop_body_update_counter(postgres_4c_gate)
        (proxy_markers / "stop").write_text("1\n", encoding="utf-8")
        stdout, stderr = proxy.communicate(timeout=TIMEOUT_SECONDS)
        assert proxy.returncode == 0, f"proxy failed\n{stdout}\n{stderr}"


def test_unlanded_commit_stays_pending_then_fresh_process_prechecks_before_body(
    postgres_4c_gate: Postgres4CGate,
    seed_4c_episode: object,
    tmp_path: Path,
) -> None:
    seed = seed_4c_episode("unlanded-commit")  # type: ignore[operator]
    workflow_id = _workflow_id("unlanded-commit")
    executor_id = "4c-unlanded-commit"
    proxy_markers = tmp_path / "unlanded-commit-proxy"
    proxy_markers.mkdir()
    target = make_url(postgres_4c_gate.admin_dsn)
    proxy = subprocess.Popen(
        [
            sys.executable,
            str(TCP_PROXY),
            "--target-host",
            str(target.host),
            "--target-port",
            str(target.port),
            "--markers",
            str(proxy_markers),
            "--cut",
            "before",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    ready = json.loads(_wait_file(proxy_markers / "ready", proxy).read_text())
    proxy_url = (
        make_url(postgres_4c_gate.runtime_dsn)
        .set(host=ready["host"], port=ready["port"])
        .update_query_dict({"sslmode": "disable"})
    )
    (proxy_markers / "arm").write_text("1\n", encoding="utf-8")
    _install_body_update_counter(postgres_4c_gate)
    original = _start_worker(
        tmp_path,
        postgres_4c_gate,
        seed,
        label="unlanded-commit-original",
        workflow_id=workflow_id,
        executor_id=executor_id,
        datasource_dsn=proxy_url.render_as_string(hide_password=False),
        mode="body_count",
    )
    try:
        _wait_marker(original, "ready")
        _wait_file(proxy_markers / "reached", proxy)
        _wait_file(proxy_markers / "fired", proxy)
        _wait_file(proxy_markers / "reconciled", proxy)
        _wait_marker(original, "error")
        _finish(original, expected_returncode=70)
        assert "_TransactionRecoveryRequired" in (original.markers / "error").read_text(
            encoding="utf-8"
        )
        with postgres_4c_gate.admin_engine.connect() as connection:
            attempts_before_recovery = connection.scalar(
                sa.text("SELECT last_value FROM eva_4c_fault.attempts")
            )
            workflow = connection.execute(
                sa.text(
                    "SELECT status, output, error FROM dbos.workflow_status "
                    "WHERE workflow_uuid=:workflow"
                ),
                {"workflow": workflow_id},
            ).one()
            datasource_rows = connection.scalar(
                sa.text(
                    "SELECT count(*) FROM dbos.datasource_outputs "
                    "WHERE workflow_id=:workflow"
                ),
                {"workflow": workflow_id},
            )
            operation_rows = connection.scalar(
                sa.text(
                    "SELECT count(*) FROM dbos.operation_outputs "
                    "WHERE workflow_uuid=:workflow"
                ),
                {"workflow": workflow_id},
            )
        assert attempts_before_recovery == 1
        assert (original.markers / "body_entries").read_text().splitlines() == ["body"]
        assert tuple(workflow) == ("PENDING", None, None)
        assert datasource_rows == 0
        assert operation_rows == 0
        assert _transition_counts(postgres_4c_gate, seed) == (1, 0)

        recovered = _start_worker(
            tmp_path,
            postgres_4c_gate,
            seed,
            label="unlanded-commit-recovery",
            workflow_id=workflow_id,
            executor_id=executor_id,
            action="recover",
            mode="trace_recovery",
        )
        _finish(recovered)
        trace = (recovered.markers / "trace").read_text(encoding="utf-8").splitlines()
        assert trace.index("precheck") < trace.index("body")
        with postgres_4c_gate.admin_engine.connect() as connection:
            attempts_after_recovery = connection.scalar(
                sa.text("SELECT last_value FROM eva_4c_fault.attempts")
            )
        assert attempts_after_recovery == 2
        assert _transition_counts(postgres_4c_gate, seed) == (2, 1)
        assert (recovered.markers / "result.json").read_bytes() == canonical_json_bytes(
            seed.expected_result
        )
        _assert_atomic_result(postgres_4c_gate, seed, workflow_id)
    finally:
        if original.process.poll() is None:
            original.process.kill()
            original.process.communicate()
        _drop_body_update_counter(postgres_4c_gate)
        (proxy_markers / "stop").write_text("1\n", encoding="utf-8")
        stdout, stderr = proxy.communicate(timeout=TIMEOUT_SECONDS)
        assert proxy.returncode == 0, f"proxy failed\n{stdout}\n{stderr}"


def _pending_checkpoint_counts(
    gate: Postgres4CGate, workflow_id: str
) -> tuple[str, object, object, int, int]:
    with gate.admin_engine.connect() as connection:
        workflow = connection.execute(
            sa.text(
                "SELECT status, output, error FROM dbos.workflow_status "
                "WHERE workflow_uuid=:workflow"
            ),
            {"workflow": workflow_id},
        ).one()
        datasource_rows = connection.scalar(
            sa.text(
                "SELECT count(*) FROM dbos.datasource_outputs "
                "WHERE workflow_id=:workflow"
            ),
            {"workflow": workflow_id},
        )
        operation_rows = connection.scalar(
            sa.text(
                "SELECT count(*) FROM dbos.operation_outputs "
                "WHERE workflow_uuid=:workflow"
            ),
            {"workflow": workflow_id},
        )
    return (
        workflow.status,
        workflow.output,
        workflow.error,
        int(datasource_rows),
        int(operation_rows),
    )


@pytest.mark.parametrize(
    ("mode", "label", "original_update_called", "final_update_attempts"),
    [
        ("block_before_precheck", "database-down-before-body", False, 1),
        ("block_after_event", "database-down-midtransaction", True, 2),
    ],
    ids=["before-body", "midtransaction"],
)
def test_real_database_route_loss_returns_pending_then_fresh_process_recovers(
    postgres_4c_gate: Postgres4CGate,
    seed_4c_episode: object,
    tmp_path: Path,
    mode: str,
    label: str,
    original_update_called: bool,
    final_update_attempts: int,
) -> None:
    seed = seed_4c_episode(label)  # type: ignore[operator]
    workflow_id = _workflow_id(label)
    executor_id = f"4c-{label}"
    proxy_markers = tmp_path / f"{label}-route-proxy"
    proxy_markers.mkdir()
    target = make_url(postgres_4c_gate.admin_dsn)
    proxy = subprocess.Popen(
        [
            sys.executable,
            str(ROUTE_CUT_PROXY),
            "--target-host",
            str(target.host),
            "--target-port",
            str(target.port),
            "--markers",
            str(proxy_markers),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    ready = json.loads(_wait_file(proxy_markers / "ready", proxy).read_text())
    proxy_url = (
        make_url(postgres_4c_gate.runtime_dsn)
        .set(host=ready["host"], port=ready["port"])
        .update_query_dict({"sslmode": "disable"})
    )
    (proxy_markers / "arm").write_text("1\n", encoding="utf-8")
    _install_body_update_counter(postgres_4c_gate)
    original = _start_worker(
        tmp_path,
        postgres_4c_gate,
        seed,
        label=f"{label}-original",
        workflow_id=workflow_id,
        executor_id=executor_id,
        datasource_dsn=proxy_url.render_as_string(hide_password=False),
        mode=mode,
    )
    (original.markers / "armed").write_text("1\n", encoding="utf-8")
    try:
        _wait_marker(original, "reached")
        (proxy_markers / "cut").write_text("1\n", encoding="utf-8")
        _wait_file(proxy_markers / "reached", proxy)
        _wait_file(proxy_markers / "fired", proxy)
        (original.markers / "release").write_text("1\n", encoding="utf-8")
        _wait_marker(original, "fired")
        _wait_file(proxy_markers / "reconciled", proxy)
        _wait_marker(original, "error")
        assert "_TransactionRecoveryRequired" in (original.markers / "error").read_text(
            encoding="utf-8"
        )
        assert (original.markers / "armed").read_text() == "1\n"
        assert (original.markers / "reached").read_text() == "1\n"
        assert (original.markers / "fired").read_text() == "1\n"

        _finish(original, expected_returncode=70)

        body_entries = original.markers / "body_entries"
        if mode == "block_before_precheck":
            assert not body_entries.exists()
        else:
            assert body_entries.read_text().splitlines() == ["body"]
        assert _transition_counts(postgres_4c_gate, seed) == (1, 0)
        assert _pending_checkpoint_counts(postgres_4c_gate, workflow_id) == (
            "PENDING",
            None,
            None,
            0,
            0,
        )
        with postgres_4c_gate.admin_engine.connect() as connection:
            counter = connection.execute(
                sa.text("SELECT last_value, is_called FROM eva_4c_fault.attempts")
            ).one()
        assert counter.is_called is original_update_called

        recovered = _start_worker(
            tmp_path,
            postgres_4c_gate,
            seed,
            label=f"{label}-recovery",
            workflow_id=workflow_id,
            executor_id=executor_id,
            action="recover",
            mode="trace_recovery",
        )
        _finish(recovered)
        trace = (recovered.markers / "trace").read_text().splitlines()
        assert trace.index("precheck") < trace.index("body")
        with postgres_4c_gate.admin_engine.connect() as connection:
            attempts = connection.scalar(
                sa.text("SELECT last_value FROM eva_4c_fault.attempts")
            )
        assert attempts == final_update_attempts
        assert _transition_counts(postgres_4c_gate, seed) == (2, 1)
        assert (recovered.markers / "result.json").read_bytes() == canonical_json_bytes(
            seed.expected_result
        )
        _assert_atomic_result(postgres_4c_gate, seed, workflow_id)
    finally:
        if original.process.poll() is None:
            original.process.kill()
            original.process.communicate()
        _drop_body_update_counter(postgres_4c_gate)
        (proxy_markers / "stop").write_text("1\n", encoding="utf-8")
        stdout, stderr = proxy.communicate(timeout=TIMEOUT_SECONDS)
        assert proxy.returncode == 0, f"route proxy failed\n{stdout}\n{stderr}"


def test_sigkill_after_event_before_datasource_result_rolls_back_then_recovers(
    postgres_4c_gate: Postgres4CGate,
    seed_4c_episode: object,
    tmp_path: Path,
) -> None:
    seed = seed_4c_episode("sigkill-before-result")  # type: ignore[operator]
    workflow_id = _workflow_id("sigkill-before-result")
    executor_id = "4c-sigkill-before-result"
    _install_body_update_counter(postgres_4c_gate)
    original = _start_worker(
        tmp_path,
        postgres_4c_gate,
        seed,
        label="sigkill-before-result-original",
        workflow_id=workflow_id,
        executor_id=executor_id,
        mode="block_after_event",
    )
    try:
        _wait_marker(original, "reached")
        assert (original.markers / "body_entries").read_text().splitlines() == ["body"]
        original.process.kill()
        original.process.communicate(timeout=TIMEOUT_SECONDS)
        (original.markers / "killed").write_text("SIGKILL\n", encoding="utf-8")
        assert (original.markers / "killed").read_text() == "SIGKILL\n"
        assert original.process.returncode == -9
        assert _transition_counts(postgres_4c_gate, seed) == (1, 0)
        assert _pending_checkpoint_counts(postgres_4c_gate, workflow_id) == (
            "PENDING",
            None,
            None,
            0,
            0,
        )

        recovered = _start_worker(
            tmp_path,
            postgres_4c_gate,
            seed,
            label="sigkill-before-result-recovery",
            workflow_id=workflow_id,
            executor_id=executor_id,
            action="recover",
            mode="trace_recovery",
        )
        _finish(recovered)
        trace = (recovered.markers / "trace").read_text().splitlines()
        assert trace.index("precheck") < trace.index("body")
        with postgres_4c_gate.admin_engine.connect() as connection:
            attempts = connection.scalar(
                sa.text("SELECT last_value FROM eva_4c_fault.attempts")
            )
        assert attempts == 2
        assert _transition_counts(postgres_4c_gate, seed) == (2, 1)
        _assert_atomic_result(postgres_4c_gate, seed, workflow_id)
    finally:
        if original.process.poll() is None:
            original.process.kill()
            original.process.communicate()
        _drop_body_update_counter(postgres_4c_gate)


def test_sigkill_after_observer_visible_commit_replays_without_body(
    postgres_4c_gate: Postgres4CGate,
    seed_4c_episode: object,
    tmp_path: Path,
) -> None:
    seed = seed_4c_episode("sigkill-after-commit")  # type: ignore[operator]
    workflow_id = _workflow_id("sigkill-after-commit")
    executor_id = "4c-sigkill-after-commit"
    _install_body_update_counter(postgres_4c_gate)
    original = _start_worker(
        tmp_path,
        postgres_4c_gate,
        seed,
        label="sigkill-after-commit-original",
        workflow_id=workflow_id,
        executor_id=executor_id,
        mode="block_after_commit",
    )
    try:
        _wait_marker(original, "reached")
        _assert_atomic_result(postgres_4c_gate, seed, workflow_id)
        (original.markers / "observer-visible").write_text("1\n", encoding="utf-8")
        assert (original.markers / "body_entries").read_text().splitlines() == ["body"]
        original.process.kill()
        original.process.communicate(timeout=TIMEOUT_SECONDS)
        (original.markers / "killed").write_text("SIGKILL\n", encoding="utf-8")
        assert (original.markers / "killed").read_text() == "SIGKILL\n"
        assert original.process.returncode == -9
        assert _pending_checkpoint_counts(postgres_4c_gate, workflow_id) == (
            "PENDING",
            None,
            None,
            1,
            0,
        )

        recovered = _start_worker(
            tmp_path,
            postgres_4c_gate,
            seed,
            label="sigkill-after-commit-recovery",
            workflow_id=workflow_id,
            executor_id=executor_id,
            action="recover",
            mode="trace_recovery",
        )
        _finish(recovered)
        trace = (recovered.markers / "trace").read_text().splitlines()
        assert trace and set(trace) == {"precheck"}
        assert not (recovered.markers / "body").exists()
        with postgres_4c_gate.admin_engine.connect() as connection:
            attempts = connection.scalar(
                sa.text("SELECT last_value FROM eva_4c_fault.attempts")
            )
        assert attempts == 1
        assert _transition_counts(postgres_4c_gate, seed) == (2, 1)
        assert (recovered.markers / "result.json").read_bytes() == canonical_json_bytes(
            seed.expected_result
        )
        _assert_atomic_result(postgres_4c_gate, seed, workflow_id)
    finally:
        if original.process.poll() is None:
            original.process.kill()
            original.process.communicate()
        _drop_body_update_counter(postgres_4c_gate)
