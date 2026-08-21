from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from eva.operations.migration_admission import (
    INITIAL_ADMISSION_STATEMENTS,
    InitialCatalogAdmissionError,
    run_initial_catalog_admission,
)


ROOT = Path(__file__).resolve().parents[2]
FREEZE = ROOT / "design/vertical/noor-4d-schema-export-freeze-v1.json"


class _Mappings:
    def __init__(self, row: dict[str, object]) -> None:
        self._row = row

    def one(self) -> dict[str, object]:
        return self._row


class _Result:
    def __init__(self, row: dict[str, object] | None = None) -> None:
        self._row = row or {}

    def mappings(self) -> _Mappings:
        return _Mappings(self._row)


class _Connection:
    def __init__(self, counts: dict[str, object]) -> None:
        self.counts = counts
        self.sql: list[str] = []

    def exec_driver_sql(self, statement: str) -> _Result:
        self.sql.append(statement)
        ordinal = len(self.sql)
        if ordinal == 4:
            return _Result(
                {
                    "session_user_name": "postgres",
                    "current_user_name": "postgres",
                    "unrestricted_rls_read": True,
                    "may_set_migrator": True,
                }
            )
        if ordinal == 5:
            return _Result({"version_num": "4b_0002_episode_atomicity"})
        if ordinal == 7:
            return _Result(self.counts)
        if ordinal == 9:
            return _Result(
                {
                    "session_user_name": "postgres",
                    "current_user_name": "eva_migrator",
                }
            )
        return _Result()


def _zero_counts() -> dict[str, object]:
    return {
        "tenants_row_count": 0,
        "installations_row_count": 0,
        "episodes_row_count": 0,
        "episode_events_row_count": 0,
    }


def test_admission_uses_the_exact_nine_sealed_statements() -> None:
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    sealed = freeze["migrationFreeze"]["initialAdmissionPrecondition"]
    expected = sealed["orderedPreDdlStatements"]

    assert [item.name for item in INITIAL_ADMISSION_STATEMENTS] == [
        item["name"] for item in expected
    ]
    assert [item.sql for item in INITIAL_ADMISSION_STATEMENTS] == [
        item["sqlUtf8"] for item in expected
    ]
    assert [
        hashlib.sha256(item.sql.encode()).hexdigest()
        for item in INITIAL_ADMISSION_STATEMENTS
    ] == [item["sqlUtf8Sha256"] for item in expected]


def test_empty_catalog_is_admitted_and_switches_role_only_after_count() -> None:
    connection = _Connection(_zero_counts())

    decision = run_initial_catalog_admission(connection)  # type: ignore[arg-type]

    assert decision.decision == "admitted_empty"
    assert decision.row_counts == _zero_counts()
    assert connection.sql == [item.sql for item in INITIAL_ADMISSION_STATEMENTS]


def test_any_preserved_row_rejects_before_role_switch_and_ddl() -> None:
    counts = _zero_counts()
    counts["episode_events_row_count"] = 1
    connection = _Connection(counts)

    with pytest.raises(InitialCatalogAdmissionError) as caught:
        run_initial_catalog_admission(connection)  # type: ignore[arg-type]

    assert caught.value.code == "eva4d_initial_admission_nonempty"
    assert caught.value.row_counts == counts
    assert connection.sql == [item.sql for item in INITIAL_ADMISSION_STATEMENTS[:7]]


@pytest.mark.parametrize(
    "counts",
    (
        {**_zero_counts(), "extra": 0},
        {
            key: value
            for key, value in _zero_counts().items()
            if key != "tenants_row_count"
        },
        {**_zero_counts(), "tenants_row_count": True},
        {**_zero_counts(), "tenants_row_count": -1},
        {**_zero_counts(), "tenants_row_count": "0"},
    ),
)
def test_unprovable_count_shape_fails_closed(counts: dict[str, object]) -> None:
    connection = _Connection(counts)

    with pytest.raises(InitialCatalogAdmissionError) as caught:
        run_initial_catalog_admission(connection)  # type: ignore[arg-type]

    assert caught.value.code == "eva4d_initial_admission_unprovable"
    assert connection.sql == [item.sql for item in INITIAL_ADMISSION_STATEMENTS[:7]]
