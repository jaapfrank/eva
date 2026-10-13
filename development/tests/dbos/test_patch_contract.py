from __future__ import annotations

import hashlib
import inspect
import json
from importlib.metadata import version
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError

from dbos import SQLAlchemyDatasource
from dbos._datasource import AsyncSQLAlchemyDatasource
from dbos._error import DBOSException


PATCH_ROOT = Path(__file__).resolve().parents[3] / "src/vendor" / "dbos-2.30.0-eva1"


def test_locked_dbos_sync_datasource_exposes_bounded_no_ddl_api() -> None:
    """The stock 2.30.0 API is the deliberate RED for the 4C fork."""

    assert version("dbos") in {"2.30.0", "2.30.0+eva.1"}
    parameters = inspect.signature(SQLAlchemyDatasource.create).parameters

    assert "run_migrations" in parameters
    assert "max_transaction_attempts" in parameters
    assert parameters["run_migrations"].default is True
    assert parameters["max_transaction_attempts"].default == 3
    assert version("dbos") == "2.30.0+eva.1"


def test_async_datasource_is_explicitly_not_patched() -> None:
    parameters = inspect.signature(AsyncSQLAlchemyDatasource.create).parameters

    assert "run_migrations" not in parameters
    assert "max_transaction_attempts" not in parameters


def test_pre_migrated_open_uses_only_a_read_statement() -> None:
    engine = sa.create_engine("sqlite://")
    provisioned = SQLAlchemyDatasource.create("sqlite://", engine=engine)
    statements: list[str] = []

    event.listen(
        engine,
        "before_cursor_execute",
        lambda _conn, _cursor, statement, _parameters, _context, _many: statements.append(
            statement
        ),
    )
    runtime = SQLAlchemyDatasource.create(
        "sqlite://",
        engine=engine,
        run_migrations=False,
        max_transaction_attempts=3,
    )

    assert runtime is not provisioned
    assert len(statements) == 1
    assert statements[0].lstrip().upper().startswith("SELECT")
    assert "datasource_outputs" in statements[0]


@pytest.mark.parametrize("attempts", [0, -1, True, 1.5])
def test_attempt_limit_rejects_non_positive_or_non_integer_values(
    attempts: object,
) -> None:
    with pytest.raises(DBOSException, match="integer >= 1"):
        SQLAlchemyDatasource.create(
            "sqlite://",
            max_transaction_attempts=attempts,  # type: ignore[arg-type]
        )


def test_mechanical_attempt_loop_cap_is_three() -> None:
    """A unit seam checks the cap; real PG tests prove 40001/40P01 routing."""

    class SyntheticSerializationFailure(Exception):
        pass

    datasource = SQLAlchemyDatasource.create("sqlite://", max_transaction_attempts=3)
    datasource._is_serialization_error = (  # type: ignore[method-assign]
        lambda error: isinstance(error, SyntheticSerializationFailure)
    )
    calls = 0

    def body() -> None:
        nonlocal calls
        calls += 1
        raise SyntheticSerializationFailure

    with pytest.raises(SyntheticSerializationFailure):
        datasource.run_tx_step(None, body)

    assert calls == 3


def test_outside_workflow_connection_error_is_not_retried() -> None:
    """This is not a reconciliation proof; the TCP/PG workflow gate is authoritative."""
    datasource = SQLAlchemyDatasource.create("sqlite://", max_transaction_attempts=3)
    calls = 0

    def body() -> None:
        nonlocal calls
        calls += 1
        raise DBAPIError(
            statement="COMMIT",
            params=None,
            orig=ConnectionError("synthetic acknowledgement loss"),
            connection_invalidated=True,
        )

    with pytest.raises(DBAPIError):
        datasource.run_tx_step(None, body)

    assert calls == 1


def test_durable_precheck_has_a_numeric_attempt_cap() -> None:
    datasource = SQLAlchemyDatasource.create("sqlite://", max_transaction_attempts=3)
    calls = 0

    def unavailable(_workflow_id: str, _step_id: int) -> None:
        nonlocal calls
        calls += 1
        raise DBAPIError(
            statement="SELECT",
            params=None,
            orig=ConnectionError("synthetic disconnect"),
            connection_invalidated=True,
        )

    datasource._check_execution = unavailable  # type: ignore[method-assign]
    recovery_required = getattr(
        __import__("dbos._datasource", fromlist=["x"]),
        "_TransactionRecoveryRequired",
    )

    with pytest.raises(recovery_required):
        datasource._check_execution_with_retry("workflow", 0)

    assert calls == 3


def test_4c_overlay_lock_binds_output_and_upstream_provenance() -> None:
    manifest = json.loads((PATCH_ROOT / "manifest.json").read_bytes())
    upstream = manifest["upstream"]
    output = manifest["output_wheel"]
    lock = (PATCH_ROOT / "requirements-4c.lock").read_text(encoding="utf-8")

    assert upstream == {
        "repository": "https://github.com/dbos-inc/dbos-transact-py",
        "tag": "2.30.0",
        "tag_url": "https://github.com/dbos-inc/dbos-transact-py/tree/2.30.0",
        "signed_commit": "e0b742c2b9100676ea4b92cc71716e0b4ffa6108",
        "sdist": {
            "filename": "dbos-2.30.0.tar.gz",
            "sha256": "777c30908360707de6a95678649016d277f49eafceddff88e732d2df4145a06f",
        },
        "license": {
            "spdx": "MIT",
            "source_url": "https://raw.githubusercontent.com/dbos-inc/dbos-transact-py/2.30.0/LICENSE",
        },
    }
    assert lock == (f"./dist/{output['filename']} --hash=sha256:{output['sha256']}\n")
    patch_path = PATCH_ROOT / manifest["patch"]["filename"]
    assert (
        hashlib.sha256(patch_path.read_bytes()).hexdigest()
        == manifest["patch"]["sha256"]
    )
