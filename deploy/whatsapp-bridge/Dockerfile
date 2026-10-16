FROM node:22.22.3-bookworm-slim@sha256:e21fc383b50d5347dc7a9f1cae45b8f4e2f0d39f7ade28e4eef7d2934522b752
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/eva-whatsapp
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund
COPY *.mjs ./
RUN mkdir -p /var/lib/eva-whatsapp && chown 65532:65532 /var/lib/eva-whatsapp && chmod 700 /var/lib/eva-whatsapp
USER 65532:65532
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=6 CMD ["node", "--input-type=module", "-e", "import {tokenFromFile} from './core.mjs'; const token=tokenFromFile(process.env.EVA_WHATSAPP_CONTROL_TOKEN_FILE || '/run/secrets/dashboard_access_token'); const r=await fetch('http://127.0.0.1:8080/status',{headers:{authorization:'Bearer '+token}}); if(!r.ok)process.exit(1)"]
CMD ["node", "server.mjs"]
