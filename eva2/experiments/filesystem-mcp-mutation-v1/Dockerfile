ARG CONTEXTFORGE_IMAGE=ghcr.io/ibm/mcp-context-forge@sha256:daf923c247fd569df5f8c9ee418cbc259fade564f78e0a0852937139a181c4dd
FROM ${CONTEXTFORGE_IMAGE}

ARG EVA_SOURCE_COMMIT
ARG EVA_ARTIFACT_MANIFEST_SHA256

LABEL io.eva2.proof="filesystem-mcp-mutation-v1" \
      io.eva2.source-commit="${EVA_SOURCE_COMMIT}" \
      io.eva2.artifact-manifest-sha256="${EVA_ARTIFACT_MANIFEST_SHA256}" \
      org.opencontainers.image.source="https://github.com/modelcontextprotocol/servers" \
      org.opencontainers.image.revision="a40bc270fb5ece62673f8a1196f57116d885c5eb" \
      org.opencontainers.image.version="@modelcontextprotocol/server-filesystem@2026.8.31"

USER 0:0

COPY rpm/ /opt/eva2/rpm/
RUN microdnf install -y /opt/eva2/rpm/*.rpm \
    && microdnf clean all \
    && rm -rf /opt/eva2/rpm /var/cache/dnf /var/cache/yum

WORKDIR /opt/eva2/filesystem-mcp
COPY package.json package-lock.json ./
COPY npm-cache/ /opt/eva2/npm-cache/
RUN npm ci --offline --ignore-scripts --omit=dev \
        --cache /opt/eva2/npm-cache \
    && npm cache clean --force --cache /opt/eva2/npm-cache \
    && rm -rf /opt/eva2/npm-cache \
    && chown -R 10001:10001 /opt/eva2/filesystem-mcp

COPY --chown=10001:10001 start-filesystem-bridge.sh /opt/eva2/start-filesystem-bridge.sh

USER 10001:10001
WORKDIR /proof-state
EXPOSE 9000

ENTRYPOINT ["/opt/eva2/start-filesystem-bridge.sh"]
