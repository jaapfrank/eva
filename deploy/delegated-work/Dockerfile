ARG SOURCE_DATE_EPOCH=1787480000
FROM docker.io/library/python@sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134 AS builder

ARG SOURCE_DATE_EPOCH
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}

WORKDIR /opt/eva

COPY deploy/requirements-delegated-work.lock ./requirements-delegated-work.lock
COPY wheelhouse ./wheelhouse
RUN python -m pip install --no-cache-dir --no-index --only-binary=:all: \
    --find-links=./wheelhouse --require-hashes -r deploy/requirements-delegated-work.lock \
    && python -m pip check

FROM cgr.dev/chainguard/python@sha256:ee37f5e4fb445732409626797dccb6f2a6337872def2bab48729ee61b335fa77

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/eva/src \
    PYTHONUNBUFFERED=1 \
    PATH=/usr/local/bin:/usr/bin

WORKDIR /opt/eva
# CPython and these exact OpenSSL libraries come from the same immutable stage.
COPY --from=builder /usr/lib/x86_64-linux-gnu/libcrypto.so.3 /usr/lib/x86_64-linux-gnu/libcrypto.so.3
COPY --from=builder /usr/lib/x86_64-linux-gnu/libssl.so.3 /usr/lib/x86_64-linux-gnu/libssl.so.3
COPY --from=builder /usr/lib/x86_64-linux-gnu/libcrypto.so.3 /usr/lib/libcrypto.so.3
COPY --from=builder /usr/lib/x86_64-linux-gnu/libssl.so.3 /lib/libssl.so.3
COPY --from=builder /usr/local /usr/local
COPY src ./src

USER 65532:65532

HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD ["/usr/local/bin/python", "-m", "eva.bootstrap.delegated_work_service", "--check"]

ENTRYPOINT ["/usr/local/bin/python"]
CMD ["-m", "eva.bootstrap.delegated_work_service"]
