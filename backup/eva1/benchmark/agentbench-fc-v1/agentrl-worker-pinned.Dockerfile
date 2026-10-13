# Disposable AgentRL worker image for AgentBench development only.
# Build context: the exact AgentRL checkout checked by
# check_agentbench_fc_upstream_integration.py.
ARG AGENTRL_COMMIT=6a73409d31ba695d383b978a8ad3ef400d90c054
ARG AGENTRL_GIT_ARCHIVE_SHA256=76a50942687e5646ad401adbb579e70fafb0761175e61019cc2c72cd0b7ce2ee
ARG AGENTRL_WORKER_LOCK_SHA256=ca657398711c05e5f162c81cde30bb4d978d2619a4cd4bd439e836bd378b088c
ARG AGENTRL_WORKER_WHEELHOUSE_MANIFEST_SHA256=4213492c14ee85fd9aac556877cdae2e70df3751cee5a4996ff478415129616c
FROM docker.io/library/python:3.10.18-slim-bookworm@sha256:445b9efb2c047a7ccdb30d293fd6b1aa0f62a55062dc2b69051df91e10848749

ARG AGENTRL_COMMIT
ARG AGENTRL_GIT_ARCHIVE_SHA256
ARG AGENTRL_WORKER_LOCK_SHA256
ARG AGENTRL_WORKER_WHEELHOUSE_MANIFEST_SHA256

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/agentrl

# This build context receives the separately sealed wheelhouse. It is never
# copied into AgentRL's Git archive and no dependency may be resolved online.
COPY agentrl-worker-requirements.lock ./requirements.lock
COPY agentrl-worker-wheelhouse ./wheelhouse
RUN python -m pip install --no-cache-dir --no-index --only-binary=:all: \
        --find-links=./wheelhouse --require-hashes -r requirements.lock

# The worker's build hook generates its gRPC stubs from this exact source
# directory. We deliberately install from source, not from mutable PyPI.
COPY proto ./proto
COPY worker ./worker
RUN python -m pip install --no-cache-dir --no-index --find-links=./wheelhouse \
        --no-deps --no-build-isolation ./worker

LABEL org.opencontainers.image.revision="${AGENTRL_COMMIT}" \
      org.opencontainers.image.source-archive-sha256="${AGENTRL_GIT_ARCHIVE_SHA256}" \
      io.eva.agentrl-worker-lock-sha256="${AGENTRL_WORKER_LOCK_SHA256}" \
      io.eva.agentrl-worker-wheelhouse-manifest-sha256="${AGENTRL_WORKER_WHEELHOUSE_MANIFEST_SHA256}"
USER 65532:65532
ENTRYPOINT ["python", "-m", "agentrl.worker"]
