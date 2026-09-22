# Existing pinned provider image carries MCP SDK 1.30.0 and Node.
FROM eva2.local/workspace-filesystem@sha256:bd31ea2dde76561fe662a551756f939e9007048956c300e54ad49a8c18ddfdc4
COPY workspace-writer-broker /opt/eva2/atomic/workspace-writer-broker
COPY workspace-writer-broker.sha256 broker-client.mjs project-journal.mjs relocation.mjs mcp-server.mjs /opt/eva2/atomic/
COPY start-atomic-workspace.sh /opt/eva2/atomic/
USER 10001:10001
WORKDIR /workspace
ENTRYPOINT ["/opt/eva2/atomic/start-atomic-workspace.sh"]
