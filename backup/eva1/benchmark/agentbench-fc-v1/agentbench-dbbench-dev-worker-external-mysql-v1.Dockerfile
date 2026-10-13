# Development smoke image only. It extends the sealed DBBench task inputs with
# a no-socket adapter for one caller-provisioned disposable MySQL endpoint.
# This configuration is explicitly not an official AgentBench score route.
FROM eva2/agentbench-agentrl-worker-chainguard-offline:20260827 AS agentrl_worker

FROM cgr.dev/chainguard/python@sha256:aa8fd2447b8b52922db57deb3894b622c3229387aaaec5934d64b85dbff6eb17 AS builder
USER root
WORKDIR /build
COPY --from=agentrl_worker /runtime-site /runtime-site
COPY inputs/mysql_connector_python-9.3.0-cp314-cp314-linux_x86_64.whl /inputs/
COPY inputs/agentbench-dbbench-dev-d1e4a10.tar.gz /inputs/
RUN python -m pip install --no-index --no-deps --target /runtime-site \
      /inputs/mysql_connector_python-9.3.0-cp314-cp314-linux_x86_64.whl \
    && mkdir -p /app \
    && tar -xzf /inputs/agentbench-dbbench-dev-d1e4a10.tar.gz -C /app \
    && PYTHONPATH=/runtime-site:/app python -c \
      'import mysql.connector.aio; from src.server.tasks.dbbench.task import DBBenchTask'

FROM cgr.dev/chainguard/python@sha256:ee37f5e4fb445732409626797dccb6f2a6337872def2bab48729ee61b335fa77
COPY --from=builder /runtime-site /runtime-site
COPY --from=builder /app /app
COPY agentbench-dbbench-dev-external-mysql.yaml /app/configs/tasks/dbbench-dev-external-mysql.yaml
COPY dbbench_external_task.py /app/dbbench_external_task.py
ENV PYTHONPATH="/runtime-site:/app" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
USER 65532:65532
ENTRYPOINT ["/usr/bin/python", "-m", "agentrl.worker"]
