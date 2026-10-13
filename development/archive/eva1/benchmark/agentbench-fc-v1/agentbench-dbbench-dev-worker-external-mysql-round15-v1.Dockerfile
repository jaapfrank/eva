# Networkless, single-file extension of the already sealed and scanned
# external-MySQL DBBench worker.  It changes only max_round from the earlier
# two-turn cost smoke to upstream DBBench's value 15.
# expected-local-base-id: sha256:b0f2ac1f20ecb9927a76dffc76f4ad93a590d20e359cf8fe0196f5d573c6e026
FROM eva2/agentbench-dbbench-dev-worker-chainguard-offline:20260827-external-mysql-v2

COPY agentbench-dbbench-dev-external-mysql-upstream-rounds.yaml /app/configs/tasks/dbbench-dev-external-mysql.yaml
