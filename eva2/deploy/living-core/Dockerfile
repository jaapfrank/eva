ARG SOURCE_COMMIT
ARG SOURCE_DATE_EPOCH
FROM docker.io/library/python@sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134 AS build

ARG SOURCE_COMMIT
ARG SOURCE_DATE_EPOCH
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/eva/src:/opt/eva \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}

WORKDIR /opt/eva

LABEL io.eva2.source-commit=${SOURCE_COMMIT} \
      io.eva2.source-date-epoch=${SOURCE_DATE_EPOCH} \
      io.eva2.source-refresh="git-archive-v1"

COPY requirements-4d.lock ./requirements-4d.lock
COPY wheelhouse ./wheelhouse
COPY pyproject.toml ./pyproject.toml
COPY src ./src
COPY connector_sdk ./connector_sdk
RUN python -m pip install --no-cache-dir --no-index --only-binary=:all: --find-links=./wheelhouse --require-hashes -r requirements-4d.lock \
    && python -m pip install --no-cache-dir --no-index --no-deps --no-build-isolation . \
    && python -c "from importlib import metadata; points=metadata.entry_points().select(group='eva2.external_providers'); assert any(point.name == 'mcp_gateway' for point in points)" \
    && python -m pip check \
    && python -m pip uninstall -y pip wheel \
    && python -c "import importlib.util; assert importlib.util.find_spec('pip') is None; assert importlib.util.find_spec('wheel') is None"

COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
COPY schemas ./schemas

FROM docker.io/library/python@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217 AS runtime_libs

FROM gcr.io/distroless/cc-debian13@sha256:9b615fff20e1a4fad29c2b30562580b212c7dd5e2225236735cca0070ed11c78

ARG SOURCE_COMMIT
ARG SOURCE_DATE_EPOCH
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/eva/src:/opt/eva \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/usr/local/bin:/usr/bin \
    SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}

WORKDIR /opt/eva

LABEL io.eva2.source-commit=${SOURCE_COMMIT} \
      io.eva2.source-date-epoch=${SOURCE_DATE_EPOCH} \
      io.eva2.source-refresh="git-archive-v1"

# Keep build-only Debian packages out of the released runtime. /usr/local
# contains the complete pinned CPython 3.12 installation and its sealed wheels.
COPY --from=build /usr/local /usr/local
COPY --from=build /opt/eva /opt/eva
# Python's _ctypes and bundled libpq need these two runtime libraries. They
# come from the separately pinned Python 3.12 runtime-libraries stage.
COPY --from=runtime_libs /usr/lib/x86_64-linux-gnu/libffi.so.8* /usr/lib/x86_64-linux-gnu/
COPY --from=runtime_libs /usr/lib/x86_64-linux-gnu/libz.so.1* /usr/lib/x86_64-linux-gnu/

USER 65532:65532
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD ["/usr/local/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).read()"]

ENTRYPOINT ["/usr/local/bin/python"]
CMD ["-m", "uvicorn", "eva.bootstrap.runtime:create_runtime_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
