"""One explicit SQLAlchemy driver choice for Eva's PostgreSQL connections.

The public runtime contract historically accepted both ``postgresql://`` and
``postgresql+psycopg://`` DSNs.  SQLAlchemy interprets the former as psycopg2,
which Eva deliberately does not install.  Normalising at this boundary keeps
the file-secret contract backwards-compatible while ensuring every real
connection uses the pinned psycopg v3 wheel.
"""

from __future__ import annotations


_PLAIN_PREFIX = "postgresql://"
_PSYCOPG_PREFIX = "postgresql+psycopg://"


def sqlalchemy_postgres_dsn(database_dsn: str) -> str:
    """Return the supported PostgreSQL DSN with Eva's explicit driver.

    This is deliberately a small string-level conversion: credentials,
    percent-encoding, query arguments and the actual authority stay unchanged.
    Other SQLAlchemy drivers and database backends are rejected before a
    connection attempt.
    """

    if not isinstance(database_dsn, str):
        raise ValueError("Eva database DSN must be text")
    value = database_dsn.strip()
    if value.startswith(_PSYCOPG_PREFIX):
        return value
    if value.startswith(_PLAIN_PREFIX):
        return _PSYCOPG_PREFIX + value.removeprefix(_PLAIN_PREFIX)
    raise ValueError("Eva database DSN must use PostgreSQL with psycopg")


__all__ = ["sqlalchemy_postgres_dsn"]
