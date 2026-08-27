from __future__ import annotations

import pytest

from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn


def test_plain_postgresql_dsn_uses_the_pinned_psycopg_driver() -> None:
    assert sqlalchemy_postgres_dsn("postgresql://eva:pass@postgres/eva2?sslmode=disable") == (
        "postgresql+psycopg://eva:pass@postgres/eva2?sslmode=disable"
    )


def test_explicit_psycopg_dsn_is_preserved() -> None:
    value = "postgresql+psycopg://eva:pass@postgres/eva2"
    assert sqlalchemy_postgres_dsn(value) == value


@pytest.mark.parametrize(
    "value",
    ("sqlite:///eva.db", "postgresql+psycopg2://eva@postgres/eva2", ""),
)
def test_rejects_other_database_backends_or_drivers(value: str) -> None:
    with pytest.raises(ValueError, match="PostgreSQL with psycopg"):
        sqlalchemy_postgres_dsn(value)
