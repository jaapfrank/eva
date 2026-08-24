ARG SOURCE_DATE_EPOCH=1787480000
FROM docker.io/library/python@sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134

ARG SOURCE_DATE_EPOCH
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/eva/src:/opt/eva \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}

WORKDIR /opt/eva

COPY requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock

COPY src ./src
COPY connector_sdk ./connector_sdk
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
COPY schemas ./schemas

USER 65532:65532
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).read()"]

CMD ["uvicorn", "eva.bootstrap.runtime:create_runtime_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
