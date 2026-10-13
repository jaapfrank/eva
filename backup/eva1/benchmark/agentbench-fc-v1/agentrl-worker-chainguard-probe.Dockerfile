# Temporary dependency-compatibility probe; never a production or benchmark image.
FROM cgr.dev/chainguard/python@sha256:aa8fd2447b8b52922db57deb3894b622c3229387aaaec5934d64b85dbff6eb17 AS builder
USER root
WORKDIR /src
COPY proto ./proto
COPY worker ./worker
RUN python -m pip install --no-cache-dir --target /runtime-site ./worker

FROM cgr.dev/chainguard/python@sha256:ee37f5e4fb445732409626797dccb6f2a6337872def2bab48729ee61b335fa77
COPY --from=builder /runtime-site /runtime-site
ENV PYTHONPATH="/runtime-site" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER 65532:65532
ENTRYPOINT ["/usr/bin/python", "-m", "agentrl.worker"]
