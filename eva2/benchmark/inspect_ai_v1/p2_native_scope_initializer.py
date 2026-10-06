"""Initialize only a fresh isolated P2 database and its native resource scope.

Uses existing migration/Noor/budget APIs. No final case, oracle, provider, model,
runtime reply or private evaluator implementation is loaded here.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import sys

EVA_ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(EVA_ROOT), str(EVA_ROOT / "src")]


def initialize_scope(
    database_dsn: str, *, runtime_database_dsn: str, expected_database: str, runtime_root: Path,
    budget_source: Path, target_uid: int | None = None,
) -> dict:
    import sqlalchemy as sa
    from eva.contracts.runtime_budget import RuntimeResourceBudget
    from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
    from eva.operations.install_schema import install_schema
    from tests.portable.test_portable_restore_full_schema import _exact_fixture_row
    from tests.support.noor_4d_repository import PostgresNoor4DRepository

    if expected_database != "eva_core_sample" and not re.fullmatch(r"eva2_p2_[a-z0-9_]{1,100}", expected_database):
        raise ValueError("A dedicated fresh P2 database name is required")
    url = sa.engine.make_url(database_dsn)
    if url.database != expected_database:
        raise ValueError("Database scope differs from the explicit isolated target")
    runtime_url = sa.engine.make_url(runtime_database_dsn)
    if runtime_url.database != expected_database or runtime_url.username != "eva_dbos":
        raise ValueError("Runtime requires the ordinary application role in the isolated target")
    runtime_root = runtime_root.resolve()
    if not str(runtime_root).startswith("/tmp/eva2-ev003-provider-history-"):
        raise ValueError("Runtime files must use the dedicated temporary evaluation root")
    if any((runtime_root / name).exists() for name in ("core-dsn", "runtime-budget.json")):
        raise ValueError("Existing runtime material must not be overwritten")
    if target_uid is not None and target_uid < 0:
        raise ValueError("Runtime user must be a nonnegative UID")
    engine = sa.create_engine(sqlalchemy_postgres_dsn(database_dsn))
    try:
        with engine.connect() as connection:
            tables = connection.exec_driver_sql(
                "SELECT count(*) FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE c.relkind IN ('r','p','v','m','f') "
                "AND n.nspname NOT IN ('pg_catalog','information_schema') "
                "AND left(n.nspname, 8) <> 'pg_toast'"
            ).scalar_one()
        if tables:
            raise ValueError("Only an empty isolated database may be initialized")

        raw_budget = budget_source.read_bytes()
        declared_budget = json.loads(raw_budget)
        original_budget = RuntimeResourceBudget.from_bytes(
            raw_budget, expected_tenant_id=declared_budget["tenant_id"],
            expected_installation_id=declared_budget["installation_id"],
        )
        facts = {
            name: _exact_fixture_row("static/" + name)
            for name in ("tenant", "installation", "identity-pack", "policy-pack", "principal-owner")
        }
        tenant = str(facts["tenant"].values["tenant_id"])
        installation = str(facts["installation"].values["installation_id"])
        principal = str(facts["principal-owner"].values["principal_id"])
        scoped_budget = replace(
            original_budget, tenant_id=tenant, installation_id=installation,
        )
        assert scoped_budget.budget_payload() == original_budget.budget_payload()
        assert scoped_budget.source_budget_sha256 == original_budget.source_budget_sha256

        schema = install_schema(database_dsn, alembic_ini=EVA_ROOT / "alembic.ini")
        with engine.begin() as connection:
            connection.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
            connection.exec_driver_sql("SET LOCAL ROLE eva_dbos")
            repository = PostgresNoor4DRepository(connection, tenant, installation)
            for row in facts.values():
                repository.upsert_exact(row)

        runtime_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        files = {
            "core-dsn": runtime_database_dsn.encode(),
            "runtime-budget.json": scoped_budget.to_bytes(),
        }
        for name, content in files.items():
            target = runtime_root / name
            if target.exists():
                raise ValueError("Existing runtime material must not be overwritten")
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
            if target_uid is not None:
                os.chown(target, target_uid, target_uid)
        if target_uid is not None:
            os.chown(runtime_root, target_uid, target_uid)
        return {
            "contract": "EvaP2NativeScopeInitialization/v1",
            "status": "fresh_scope_initialized", "database": expected_database,
            "tenant_id": tenant, "installation_id": installation, "principal_id": principal,
            "schemaRevision": schema.final_revision,
            "seedFacts": ["static/" + name for name in facts],
            "budgetValuesUnchanged": True, "runtimeDatabaseRole": "eva_dbos",
            "sourceBudgetSha256": original_budget.source_budget_sha256,
            "runtimeBudgetSha256": hashlib.sha256(scoped_budget.to_bytes()).hexdigest(),
            "files": list(files), "providerCalls": 0,
            "caseOrOracleLoaded": False, "personalDataCopied": False,
        }
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn-file", type=Path, required=True)
    parser.add_argument("--runtime-dsn-file", type=Path, required=True)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--budget-source", type=Path, required=True)
    parser.add_argument("--target-uid", type=int)
    args = parser.parse_args()
    try:
        receipt = initialize_scope(
            args.dsn_file.read_text().strip(),
            runtime_database_dsn=args.runtime_dsn_file.read_text().strip(),
            expected_database=args.expected_database, runtime_root=args.runtime_root,
            budget_source=args.budget_source, target_uid=args.target_uid,
        )
    except Exception as error:
        print(json.dumps({"status": "initialization_failed", "failureClass": type(error).__name__}))
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
