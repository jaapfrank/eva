ARG PROVEN_CORE_IMAGE=eva2/managed-current-core:ceaaa7d
FROM ${PROVEN_CORE_IMAGE} AS proven_core

FROM docker.io/library/python@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217 AS current_runtime_libs

FROM current_runtime_libs AS upgraded_python
COPY --from=proven_core /usr/local /usr/local
COPY wheelhouse/cryptography-50.0.1-cp311-abi3-manylinux_2_34_x86_64.whl /tmp/cryptography-50.0.1-cp311-abi3-manylinux_2_34_x86_64.whl
RUN echo '51afcfceb15597cf2635068e4ac9a56b2abde622edde17f37d85fd7b5306497a  /tmp/cryptography-50.0.1-cp311-abi3-manylinux_2_34_x86_64.whl' | sha256sum -c - \
    && python -m pip install --no-cache-dir --no-index --no-deps --force-reinstall /tmp/cryptography-50.0.1-cp311-abi3-manylinux_2_34_x86_64.whl \
    && python -c "import importlib.metadata as m; assert m.version('cryptography') == '50.0.1'" \
    && rm /tmp/cryptography-50.0.1-cp311-abi3-manylinux_2_34_x86_64.whl

FROM gcr.io/distroless/cc-debian12@sha256:9dac0a79194e45a7da0158a9c6da57b217585af0786db3845d1f0ec1a0dd182f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/eva/src:/opt/eva \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/eva
COPY --from=upgraded_python /usr/local /usr/local
COPY --from=proven_core /opt/eva /opt/eva
# Python's _ctypes and bundled libpq need libffi and zlib. Both come from the
# separately scanned current Python 3.12 trixie image; neither package appears
# among that image's HIGH/CRITICAL findings.
COPY --from=current_runtime_libs /usr/lib/x86_64-linux-gnu/libffi.so.8* /usr/lib/x86_64-linux-gnu/
COPY --from=current_runtime_libs /usr/lib/x86_64-linux-gnu/libz.so.1* /usr/lib/x86_64-linux-gnu/

USER 65532:65532
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD ["/usr/local/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).read()"]

ENTRYPOINT ["/usr/local/bin/python"]
CMD ["-m", "uvicorn", "eva.bootstrap.runtime:create_runtime_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
