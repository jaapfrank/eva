ARG SOURCE_DATE_EPOCH=1787480000
FROM docker.io/library/python@sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134

ARG SOURCE_DATE_EPOCH
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/eva/src \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}

WORKDIR /opt/eva

COPY requirements-delegated-work.lock ./requirements-delegated-work.lock
COPY wheelhouse ./wheelhouse
RUN python -m pip install --no-cache-dir --no-index --only-binary=:all: \
    --find-links=./wheelhouse --require-hashes -r requirements-delegated-work.lock \
    && rm -rf ./wheelhouse

COPY src ./src

USER 65532:65532

HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD ["python", "-m", "eva.bootstrap.delegated_work_service", "--check"]

CMD ["python", "-m", "eva.bootstrap.delegated_work_service"]
