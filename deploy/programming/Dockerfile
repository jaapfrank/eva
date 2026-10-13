FROM node@sha256:a9f5f7c91a432850b2a8a7797adf5eadb6c733ceed61167806cee7ea7fbc29df AS node
FROM eva2-m3-self-state-core@sha256:5a8d2e99712cc5fc6c676475b0080ec994ed2fd84160f107be9bdb5275755e44 AS reference
FROM python@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && apt-get update && apt-get install -y --no-install-recommends git ripgrep && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/desktop-commander
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --omit=dev --audit=false --fund=false
COPY --from=reference /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=reference /opt/eva/src/eva /opt/eva-source/eva
COPY default-config.json start.sh /opt/eva-programming/
ENV HOME=/workspace/.home PYTHONPATH=/opt/eva-source PYTHONDONTWRITEBYTECODE=1 MCP_CLIENT_DOCKER=true
USER 10001:10001
WORKDIR /workspace
ENTRYPOINT ["/bin/sh", "/opt/eva-programming/start.sh"]
