# Migrations

This is the only Alembic revision tree for `eva_*`. Runtime imports and startup
perform no DDL; only an explicit Alembic invocation may materialize the schema.

Tranche 4B has one linear, deliberately narrow chain:

1. `4b_0000_core_namespace` creates `eva_core`, validates the least-privilege
   NOLOGIN group roles and establishes schema ownership;
2. `4b_0001_tenant_scope` creates tenant and installation anchors with forced RLS;
3. `4b_0002_episode_atomicity` creates the episode head and immutable event rows
   needed by the tranche 4C datasource proof.

The migrations do not create `dbos`, any other `eva_*` namespace, connector data,
memory, projections or telemetry. PostgreSQL roles are cluster-global installation
prerequisites and are not removed by a database-local downgrade. Downgrade is
tested only on an empty disposable database before traffic; application rollback
after traffic keeps the expanded database and is a later tranche gate.
