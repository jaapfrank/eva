# Disposable AgentRL worker image for AgentBench development only.
# Build context: the exact AgentRL checkout checked by
# check_agentbench_fc_upstream_integration.py.
ARG AGENTRL_COMMIT=6a73409d31ba695d383b978a8ad3ef400d90c054
ARG AGENTRL_GIT_ARCHIVE_SHA256=76a50942687e5646ad401adbb579e70fafb0761175e61019cc2c72cd0b7ce2ee
FROM docker.io/library/python:3.10.18-slim-bookworm@sha256:445b9efb2c047a7ccdb30d293fd6b1aa0f62a55062dc2b69051df91e10848749

ARG AGENTRL_COMMIT
ARG AGENTRL_GIT_ARCHIVE_SHA256

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/agentrl

# The worker's build hook generates its gRPC stubs from this exact source
# directory. We deliberately install from source, not from mutable PyPI.
COPY proto ./proto
COPY worker ./worker
RUN python -m pip install --no-cache-dir ./worker

LABEL org.opencontainers.image.revision="${AGENTRL_COMMIT}" \
      org.opencontainers.image.source-archive-sha256="${AGENTRL_GIT_ARCHIVE_SHA256}"
USER 65532:65532
ENTRYPOINT ["python", "-m", "agentrl.worker"]
