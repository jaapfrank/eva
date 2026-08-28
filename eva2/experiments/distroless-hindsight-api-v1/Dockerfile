FROM ghcr.io/vectorize-io/hindsight@sha256:aeb0831e2ec55e659d5e69baa064c1278113e523513b8f60a1f09c5cabe59b9b AS upstream

FROM upstream AS upgraded_api
COPY wheelhouse/aiohttp-3.14.3-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl /tmp/aiohttp-3.14.3-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl
COPY wheelhouse/cryptography-50.0.1-cp311-abi3-manylinux_2_34_x86_64.whl /tmp/cryptography-50.0.1-cp311-abi3-manylinux_2_34_x86_64.whl
COPY wheelhouse/jaraco_context-6.1.0-py3-none-any.whl /tmp/jaraco_context-6.1.0-py3-none-any.whl
RUN echo '53e7b4ce82b54a8bcc71b3b67a5cbd177ca1d7f592cbc92cd38b7349f73482db  /tmp/aiohttp-3.14.3-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl' | sha256sum -c - \
    && echo '51afcfceb15597cf2635068e4ac9a56b2abde622edde17f37d85fd7b5306497a  /tmp/cryptography-50.0.1-cp311-abi3-manylinux_2_34_x86_64.whl' | sha256sum -c - \
    && echo 'a43b5ed85815223d0d3cfdb6d7ca0d2bc8946f28f30b6f3216bda070f68badda  /tmp/jaraco_context-6.1.0-py3-none-any.whl' | sha256sum -c - \
    && /usr/local/bin/uv pip install --python /app/api/.venv/bin/python --offline --no-index --no-deps --reinstall /tmp/aiohttp-3.14.3-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl /tmp/cryptography-50.0.1-cp311-abi3-manylinux_2_34_x86_64.whl /tmp/jaraco_context-6.1.0-py3-none-any.whl \
    && /app/api/.venv/bin/python -c "import importlib.metadata as m; assert m.version('aiohttp') == '3.14.3'; assert m.version('cryptography') == '50.0.1'; assert m.version('jaraco.context') == '6.1.0'"

FROM docker.io/library/python@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217 AS current_runtime_libs

FROM gcr.io/distroless/cc-debian13@sha256:9b615fff20e1a4fad29c2b30562580b212c7dd5e2225236735cca0070ed11c78

ENV HOME=/home/hindsight \
    PATH=/app/api/.venv/bin:/usr/local/bin \
    HINDSIGHT_API_HOST=0.0.0.0 \
    HINDSIGHT_API_PORT=8888 \
    HINDSIGHT_API_LOG_LEVEL=info \
    LD_LIBRARY_PATH=/home/hindsight/.pg0/installation/18.1.0/lib \
    PYTHONUNBUFFERED=1 \
    TRANSFORMERS_VERBOSITY=error \
    HF_HUB_VERBOSITY=error \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app/api
COPY --from=upstream /usr/local /usr/local
COPY --from=upgraded_api /app/api /app/api
COPY --from=upstream --chown=1000:1000 /home/hindsight /home/hindsight
COPY --from=upstream /etc/passwd /etc/passwd
COPY --from=upstream /etc/group /etc/group
COPY --from=current_runtime_libs /usr/lib/x86_64-linux-gnu/libffi.so.8* /usr/lib/x86_64-linux-gnu/
COPY --from=current_runtime_libs /usr/lib/x86_64-linux-gnu/libz.so.1* /usr/lib/x86_64-linux-gnu/
COPY --from=upstream /usr/lib/x86_64-linux-gnu/libzstd.so.1* /usr/lib/x86_64-linux-gnu/
COPY --from=upstream /usr/lib/x86_64-linux-gnu/liblz4.so.1* /usr/lib/x86_64-linux-gnu/
COPY --from=upstream /usr/lib/x86_64-linux-gnu/libgssapi_krb5.so.2* /usr/lib/x86_64-linux-gnu/
COPY --from=upstream /usr/lib/x86_64-linux-gnu/libxxhash.so.0* /usr/lib/x86_64-linux-gnu/
COPY --from=upstream /usr/lib/x86_64-linux-gnu/liblzma.so.5* /usr/lib/x86_64-linux-gnu/
COPY --from=upstream /usr/lib/x86_64-linux-gnu/libkrb5.so.3* /usr/lib/x86_64-linux-gnu/
COPY --from=upstream /usr/lib/x86_64-linux-gnu/libk5crypto.so.3* /usr/lib/x86_64-linux-gnu/
COPY --from=upstream /usr/lib/x86_64-linux-gnu/libcom_err.so.2* /usr/lib/x86_64-linux-gnu/
COPY --from=upstream /usr/lib/x86_64-linux-gnu/libkrb5support.so.0* /usr/lib/x86_64-linux-gnu/
COPY --from=upstream /usr/lib/x86_64-linux-gnu/libkeyutils.so.1* /usr/lib/x86_64-linux-gnu/

USER 1000:1000
EXPOSE 8888
ENTRYPOINT ["/app/api/.venv/bin/hindsight-api"]
