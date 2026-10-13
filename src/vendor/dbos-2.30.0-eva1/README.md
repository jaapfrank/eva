# DBOS 2.30.0 Eva patch 1

This directory describes a build-time, one-member patch over the exact DBOS
2.30.0 wheel pinned in `requirements.lock`. It is not a runtime monkeypatch.
`build_dbos_4c_wheel.py` rejects any wheel, patch, or source member whose bytes do
not match `manifest.json` before producing `dbos-2.30.0+eva.1`.

The patch changes only the synchronous `SQLAlchemyDatasource`:

- `create(..., run_migrations=False)` opens a pre-migrated datasource after a
  read-only, exact-column preflight of `dbos.datasource_outputs`;
- `max_transaction_attempts` is a positive integer and defaults to three;
- transaction bodies are locally retried only for PostgreSQL `40001` and
  `40P01`;
- a connection or commit-acknowledgement ambiguity is reconciled on a fresh
  connection; a durable result is replayed, otherwise a DBOS internal
  `BaseException` leaves the workflow pending for supervised recovery;
- all pre-check retry loops are numerically bounded.

`AsyncSQLAlchemyDatasource` is deliberately unchanged and unsupported by Eva
4C. Adding parity requires a separate async cancellation and fault matrix.

Provisioning is separate from measured runtime. A provisioning identity may call
`SQLAlchemyDatasource.create(..., run_migrations=True)` to create the DBOS-owned
schema/table. The runtime identity must call the Eva adapter, which fixes
`run_migrations=False`. Eva Alembic owns only `eva_*` schemas and does not own
`dbos.datasource_outputs`.

## Reproducible 4C overlay

The repository-wide dependency lock remains the frozen 4A baseline. This
tranche therefore has a separate, hash-locked overlay and makes no claim that a
stock environment can run 4C. From this directory, build and install it with:

```sh
python ../../tools/build_dbos_4c_wheel.py \
  /path/to/dbos-2.30.0-py3-none-any.whl dist
python -m pip install --no-deps --require-hashes \
  --target /path/to/eva2-4c-site -r requirements-4c.lock
```

The builder first binds the exact upstream wheel, pristine member, patch and
patched member, then refuses an output whose hash differs from the declared
wheel. The runtime opener additionally requires distribution version
`2.30.0+eva.1` and the exact patched `_datasource.py` bytes. The real 4C gate
runs with `/path/to/eva2-4c-site` first on `PYTHONPATH`, so it cannot silently
fall back to the stock lock.

Upstream provenance is fixed in `manifest.json`: DBOS repository/tag, signed
commit, wheel and sdist hashes, and the upstream MIT license location.
