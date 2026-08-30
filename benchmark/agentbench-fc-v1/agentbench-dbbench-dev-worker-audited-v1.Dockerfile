# expected-local-base-id: sha256:18251c8749e53f3762e683e3ab9d954eb704faf3939e9fcec9c0b0ec75430c93
FROM eva2/agentbench-dbbench-dev-worker-chainguard-offline:20260830-external-mysql-round15

COPY benchmark/audit_agentbench_dbbench_environment.py /app/audit_agentbench_dbbench_environment.py
