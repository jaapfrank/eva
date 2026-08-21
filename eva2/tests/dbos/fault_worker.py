"""One-process DBOS worker used only by the real PostgreSQL 4C fault gate."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from dbos import PortableWorkflowError
from sqlalchemy import event

from eva.adapters.dbos.datasource import open_runtime_datasource
from eva.bootstrap.episode_transition_4c import configure_episode_transition_runtime
from eva.contracts.canonical_json import canonical_json_bytes


FAULT_TIMEOUT_SECONDS = 10.0


def _mark(path: Path, value: str = "1\n") -> None:
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _wait(path: Path) -> None:
    deadline = time.monotonic() + FAULT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)
    raise TimeoutError(f"fault release was not observed within 10s: {path}")


def _trace(path: Path, value: str) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(value + "\n")
        stream.flush()


def _instrument(datasource: Any, mode: str, markers: Path, fault_attempts: int) -> None:
    reached = markers / "reached"
    fired = markers / "fired"
    release = markers / "release"
    body = markers / "body"
    update_attempt = 0

    if mode in {
        "body_marker",
        "block_before_precheck",
        "barrier_before_update",
        "sentinel_after_event",
        "block_after_event",
        "trace_recovery",
        "real_40001",
        "real_40p01",
        "body_count",
        "block_after_commit",
    }:

        @event.listens_for(datasource.engine, "before_cursor_execute")
        def before_cursor_execute(
            connection: Any,
            _cursor: Any,
            statement: str,
            _parameters: Any,
            _context: Any,
            _executemany: bool,
        ) -> None:
            nonlocal update_attempt
            normalized = " ".join(statement.lower().split())
            body_entry = (
                normalized.startswith("select")
                and "from eva_core.episode_events" in normalized
            )
            if body_entry:
                _trace(markers / "body_entries", "body")
            if (
                mode == "trace_recovery"
                and "from dbos.datasource_outputs" in normalized
                and "workflow_id" in normalized
                and "step_id" in normalized
            ):
                _trace(markers / "trace", "precheck")
            if (
                mode == "block_before_precheck"
                and "from dbos.datasource_outputs" in normalized
                and "workflow_id" in normalized
                and "step_id" in normalized
                and not fired.exists()
            ):
                _mark(reached)
                _wait(release)
                _mark(fired)
            if "eva_core.episode_events" in normalized and not body.exists():
                _mark(body)
                if mode == "trace_recovery":
                    _trace(markers / "trace", "body")
            if (
                mode == "barrier_before_update"
                and normalized.startswith("update eva_core.episodes")
                and not fired.exists()
            ):
                _mark(reached)
                _wait(release)
                _mark(fired)
            if mode == "real_40001" and normalized.startswith(
                "update eva_core.episodes"
            ):
                update_attempt += 1
                _mark(markers / f"attempt-{update_attempt}")
                if update_attempt <= fault_attempts:
                    _mark(markers / f"reached-{update_attempt}")
                    _wait(markers / f"release-{update_attempt}")
                    _mark(markers / f"fired-{update_attempt}")
            if mode == "real_40p01" and normalized.startswith(
                "update eva_core.episodes"
            ):
                update_attempt += 1
                _mark(markers / f"attempt-{update_attempt}")
                if update_attempt <= fault_attempts:
                    _mark(markers / f"awaiting-{update_attempt}")
                    _wait(markers / f"blocker-ready-{update_attempt}")
                    connection.exec_driver_sql(
                        "SELECT id FROM eva_4c_deadlock.locks " "WHERE id=2 FOR UPDATE"
                    )
                    _mark(markers / f"reached-{update_attempt}")
                    connection.exec_driver_sql(
                        "SELECT id FROM eva_4c_deadlock.locks " "WHERE id=1 FOR UPDATE"
                    )

        @event.listens_for(datasource.engine, "after_cursor_execute")
        def after_cursor_execute(
            _connection: Any,
            _cursor: Any,
            statement: str,
            _parameters: Any,
            _context: Any,
            _executemany: bool,
        ) -> None:
            normalized = " ".join(statement.lower().split())
            transition_insert = normalized.startswith(
                "insert into eva_core.episode_events"
            )
            if mode == "sentinel_after_event" and transition_insert:
                _mark(reached)
                _mark(fired)
                raise PortableWorkflowError(
                    message="4C body sentinel after Eva event insert",
                    name="Eva4CBodySentinel",
                    code="body_sentinel",
                )
            if mode == "block_after_event" and transition_insert and not fired.exists():
                _mark(reached)
                _wait(release)
                _mark(fired)

    if mode == "block_after_commit":
        original_do_commit = datasource.engine.dialect.do_commit
        blocked = False

        def do_commit(dbapi_connection: Any) -> None:
            nonlocal blocked
            original_do_commit(dbapi_connection)
            if not blocked:
                blocked = True
                _mark(reached)
                _wait(release)
                _mark(fired)

        datasource.engine.dialect.do_commit = do_commit

    if mode in {"real_40001", "real_40p01"}:

        @event.listens_for(datasource.engine, "handle_error")
        def handle_error(exception_context: Any) -> None:
            sqlstate = getattr(exception_context.original_exception, "sqlstate", None)
            if sqlstate is not None:
                _trace(markers / "sqlstates", str(sqlstate))
                if sqlstate == "40P01":
                    _mark(markers / f"fired-{update_attempt}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec", type=Path)
    arguments = parser.parse_args()
    spec = json.loads(arguments.spec.read_text(encoding="utf-8"))
    markers = Path(spec["markers"])
    markers.mkdir(parents=True, exist_ok=True)
    datasource = None
    runtime = None
    try:
        datasource = open_runtime_datasource(spec["datasource_dsn"])
        raw_datasource = object.__getattribute__(
            datasource, "_EvaDBOSDatasource__datasource"
        )
        _instrument(
            raw_datasource,
            spec.get("mode", "normal"),
            markers,
            int(spec.get("fault_attempts", 0)),
        )
        runtime = configure_episode_transition_runtime(
            datasource,
            system_database_url=spec["system_dsn"],
            application_version=spec.get("application_version", "4c-gate-v1"),
            executor_id=spec["executor_id"],
        )
        runtime.launch()
        _mark(markers / "ready")
        if spec.get("action", "start") == "recover":
            result = runtime.recover(spec["workflow_id"])
        else:
            result = runtime.start(spec["workflow_id"], spec["payload"])
        (markers / "result.json").write_bytes(canonical_json_bytes(result))
        _mark(markers / "completed")
        return 0
    except BaseException as error:
        _mark(markers / "error", f"{type(error).__name__}: {error}\n")
        return 70
    finally:
        if runtime is not None:
            try:
                runtime.close()
            except BaseException:
                pass
        elif datasource is not None:
            datasource.close()


if __name__ == "__main__":
    raise SystemExit(main())
