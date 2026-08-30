# expected-local-base-id: sha256:7de640c338a0dbd6ae8db24ef054c8b4b190ac0df56734914382f1f72efdbd8f
FROM eva2/ha-lights-runtime:349e399

COPY eva2/src/eva/adapters/model/conversation.py /opt/eva/src/eva/adapters/model/conversation.py
COPY benchmark/serve_eva_tool_free_model.py /opt/eva/benchmark/serve_eva_tool_free_model.py

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["/usr/local/bin/python", "-c", "import socket; connection = socket.create_connection(('127.0.0.1', 8090), 2); connection.close()"]

ENTRYPOINT ["/usr/local/bin/python", "/opt/eva/benchmark/serve_eva_tool_free_model.py"]
CMD []
