#!/usr/bin/env bash
set -euo pipefail

# Universele 1-klik appliance-installer voor Ubuntu Server 24.04 LTS (amd64 & arm64 / Raspberry Pi).
# Maakt gebruiker/groepen, Docker Engine, Python 3.12.16, Eva2-broncode, basis-images en de
# headless Setup Wizard (poort 8765) klaar op http://eva.local:8765.

EVA_REPO_URL="${EVA_REPO_URL:-https://github.com/jaapfrank/eva.git}"
EVA_BRANCH="${EVA_BRANCH:-feat/immersive-eva-onboarding}"
EVA_REPO_DIR="${EVA_REPO_DIR:-/opt/eva2}"
EVA_RUNTIME_ROOT="${EVA_RUNTIME_ROOT:-/var/lib/eva2-local}"
EVA_WIZARD_DIR="${EVA_WIZARD_DIR:-/var/lib/eva2-appliance}"
EVA_SEED_DIR="${EVA_SEED_DIR:-/etc/eva-appliance}"
EVA_USER="${EVA_USER:-evaadmin}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Dit script moet als root worden uitgevoerd." >&2
  exit 1
fi

beep_tone() {
  printf '\a' > /dev/console 2>/dev/null || true
}

STATUS_JSON="/run/eva-bootstrap-status.json"
set_step() {
  local pct="$1"
  local msg="$2"
  local host_now
  host_now=$(hostname 2>/dev/null || echo "eva")
  local ips_json
  ips_json=$(hostname -I 2>/dev/null | awk '{printf "["; for(i=1;i<=NF;i++){if($i !~ /^127\./ && $i !~ /:/){printf "%s\"%s\"", (c++?",":""), $i}}; printf "]"}' || echo "[]")
  cat > "$STATUS_JSON" << EOFJSON
{"contract":"EvaAppliancePing/v1","eva_appliance":true,"phase":"bootstrapping","percent":$pct,"step":"$msg","hostname":"$host_now","lan_ips":$ips_json,"installation_name":"Eva wordt klaargezet"}
EOFJSON
}

set_step 8 "Stap 1/6 · Systeem voorbereiden..."
beep_tone

# Start direct een lichtgewicht statusserver op poort 8765 zodat je vanaf seconde 5 live voortgang ziet!
EARLY_PID=""
if command -v python3 >/dev/null 2>&1 && ! ss -tln 2>/dev/null | grep -q ':8765 '; then
  cat > /run/eva-early-status.py << 'PYEOF'
import http.server, json, pathlib, socketserver

STATUS_PATH = pathlib.Path("/run/eva-bootstrap-status.json")
HTML = """<!doctype html><html lang="nl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Eva wordt klaargezet…</title>
<style>
body{font:16px/1.55 system-ui,sans-serif;background:#07111c;color:#edf6ff;display:grid;min-height:100vh;place-items:center;margin:0;padding:1.5rem}
main{max-width:540px;width:100%;background:#102337;border:1px solid #2b4a66;border-radius:18px;padding:2rem}
.bar{height:12px;background:#081523;border-radius:999px;overflow:hidden;margin:1rem 0}
.fill{height:100%;background:linear-gradient(90deg,#2ea877,#62e7ff);width:10%;transition:width .5s}
.muted{color:#9fb9d0;font-size:.92rem}
</style>
<main>
  <h1 style="margin:0 0 .4rem;font-size:1.45rem">Eva wordt nu klaargezet</h1>
  <p id="step">Verbinding gevonden! Eerste installatie loopt…</p>
  <div class="bar"><div id="fill" class="fill"></div></div>
  <p class="muted">Blijf gerust op deze pagina; zodra de Eva Setup Wizard klaarstaat schakelt dit scherm automatisch door.</p>
</main>
<script>
const savedHash = location.hash || '';
setInterval(async () => {
  try {
    const r = await fetch('/internal/v1/appliance/ping', {cache:'no-store'});
    const d = await r.json();
    if (d.phase === 'ready') { location.replace('/pair' + savedHash); return; }
    if (d.step) document.getElementById('step').textContent = d.step;
    if (d.percent) document.getElementById('fill').style.width = d.percent + '%';
  } catch {}
}, 2000);
</script></html>"""

class ReuseTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()
    def do_GET(self):
        if self.path.startswith("/internal/v1/appliance/ping"):
            body = STATUS_PATH.read_bytes() if STATUS_PATH.is_file() else b'{"eva_appliance":true,"phase":"bootstrapping"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        raw = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

with ReuseTCPServer(("0.0.0.0", 8765), Handler) as srv:
    srv.serve_forever()
PYEOF
  python3 /run/eva-early-status.py >/dev/null 2>&1 &
  EARLY_PID="$!"
  trap 'if [ -n "$EARLY_PID" ]; then kill "$EARLY_PID" 2>/dev/null || true; fi' EXIT
fi

set_step 18 "Stap 1/6 · Ubuntu-pakketten, Docker en mDNS (eva.local) installeren..."
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y software-properties-common ca-certificates curl gnupg || true
add-apt-repository -y universe || true
apt-get update
apt-get install -y \
  avahi-daemon \
  ca-certificates \
  curl \
  docker.io \
  docker-compose-v2 \
  ffmpeg \
  git \
  sudo \
  ufw \
  unzip || true

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow 8765/tcp || true
  ufw allow 8766/tcp || true
fi

systemctl enable --now avahi-daemon docker

if ! id -u "$EVA_USER" >/dev/null 2>&1; then
  # Hergebruik UID 1000 als die al bestaat op het systeem.
  EXISTING_1000=$(awk -F: '$3 == 1000 {print $1}' /etc/passwd || true)
  if [ -n "$EXISTING_1000" ]; then
    EVA_USER="$EXISTING_1000"
  else
    useradd -m -u 1000 -s /bin/bash "$EVA_USER"
  fi
fi

EVA_UID=$(id -u "$EVA_USER")
EVA_HOME=$(getent passwd "$EVA_USER" | cut -d: -f6)

groupadd -f -g 65532 eva-runtime
usermod -aG docker,eva-runtime "$EVA_USER"

set_step 38 "Stap 2/6 · Node.js 22 en ChatGPT/Codex CLI installeren..."
if ! command -v node >/dev/null 2>&1; then
  (curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs) || apt-get install -y nodejs npm
fi

if ! command -v codex >/dev/null 2>&1; then
  npm install -g @openai/codex
fi

# Beperk sudoers uitsluitend tot het activeren/herstarten van Eva-services vanuit de wizard.
cat > /etc/sudoers.d/eva2-appliance << EOF
$EVA_USER ALL=(root) NOPASSWD: /usr/bin/systemctl enable eva2-local-core.service eva2-local-owner.service, /usr/bin/systemctl enable eva2-local-gateway.service, /usr/bin/systemctl restart eva2-local-gateway.service, /usr/bin/systemctl start eva2-local-gateway.service, /usr/bin/systemctl stop eva2-local-gateway.service, /usr/bin/systemctl enable eva2-local-worker.service, /usr/bin/systemctl restart eva2-local-worker.service, /usr/bin/systemctl start eva2-local-worker.service, /usr/bin/systemctl stop eva2-local-worker.service, /usr/bin/systemctl restart eva2-local-core.service, /usr/bin/systemctl start eva2-local-core.service, /usr/bin/systemctl stop eva2-local-core.service, /usr/bin/systemctl restart eva2-local-owner.service, /usr/bin/systemctl start eva2-local-owner.service, /usr/bin/systemctl stop eva2-local-owner.service, /usr/bin/systemctl restart eva2-local-core.service eva2-local-owner.service, /usr/bin/systemctl enable eva2-local-telegram.service, /usr/bin/systemctl restart eva2-local-telegram.service, /usr/bin/systemctl start eva2-local-telegram.service, /usr/bin/systemctl stop eva2-local-telegram.service
EOF
chmod 0440 /etc/sudoers.d/eva2-appliance

set_step 52 "Stap 3/6 · Eva2-broncode klaarzetten..."
if [ -f "$EVA_REPO_DIR/src/eva/bootstrap/appliance_wizard_cli.py" ]; then
  : # Vooraf uitgepakte Eva2-broncode inclusief lokale wizardbestanden behouden
elif [ -f "$EVA_SEED_DIR/eva-repo.tar.gz" ]; then
  mkdir -p "$EVA_REPO_DIR"
  tar -xzf "$EVA_SEED_DIR/eva-repo.tar.gz" -C "$EVA_REPO_DIR"
elif [ ! -d "$EVA_REPO_DIR/.git" ]; then
  git clone --branch "$EVA_BRANCH" "$EVA_REPO_URL" "$EVA_REPO_DIR" || git clone "$EVA_REPO_URL" "$EVA_REPO_DIR"
else
  git -C "$EVA_REPO_DIR" pull --ff-only || true
fi
chown -R "$EVA_UID:$EVA_UID" "$EVA_REPO_DIR"

set_step 68 "Stap 4/6 · Python 3.12.16 en AI-bibliotheken installeren..."
su -s /bin/bash "$EVA_USER" -c "
set -euo pipefail
if ! command -v uv >/dev/null 2>&1 && [ ! -x \"\$HOME/.local/bin/uv\" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH=\"\$HOME/.local/bin:\$PATH\"
uv python install 3.12.16
mkdir -p \"\$HOME/.venvs\"
uv venv --python 3.12.16 \"\$HOME/.venvs/eva\"
REQ_FILE=\$(mktemp /tmp/eva-reqs-XXXXXX.txt)
\"\$HOME/.venvs/eva/bin/python\" -c '
import tomllib, pathlib
data = tomllib.loads(pathlib.Path(\"$EVA_REPO_DIR/pyproject.toml\").read_text(encoding=\"utf-8\"))
deps = list(data[\"project\"][\"dependencies\"]) + list(data[\"project\"][\"optional-dependencies\"][\"optional-models\"]) + [\"pytest\", \"httpx\", \"edge-tts\", \"vosk\", \"websockets\"]
pathlib.Path(\"'\"\$REQ_FILE\"'\").write_text(\"\\n\".join(deps) + \"\\n\", encoding=\"utf-8\")
'
uv pip install --python \"\$HOME/.venvs/eva/bin/python\" -r \"\$REQ_FILE\"
uv pip install --python \"\$HOME/.venvs/eva/bin/python\" openai==3.46.0 pydantic-ai-slim==2.66.0 pydantic-graph==2.66.0 genai-prices==0.2.1
rm -f \"\$REQ_FILE\"
SITE_PKGS=\$(\"\\$HOME/.venvs/eva/bin/python\" -c 'import sysconfig; print(sysconfig.get_paths()[\"purelib\"])')
printf '%s/src\n%s\n' \"$EVA_REPO_DIR\" \"$EVA_REPO_DIR\" > \"\$SITE_PKGS/eva2_local.pth\"
"

set_step 88 "Stap 5/6 · Beveiligde mappen en systemd-services inrichten..."

mkdir -p "$EVA_RUNTIME_ROOT"/{secrets,pg-secret,private/restore-authority,work/model-providers,model-lease,hindsight-access,identity,wakeword}
mkdir -p "$EVA_WIZARD_DIR" "$EVA_HOME/.codex"

if [ ! -d "$EVA_RUNTIME_ROOT/wakeword/vosk-model-small-nl-0.22/conf" ]; then
  curl -fsSL "https://alphacephei.com/vosk/models/vosk-model-small-nl-0.22.zip" -o "$EVA_RUNTIME_ROOT/wakeword/vosk-nl.zip" && \
    unzip -qo "$EVA_RUNTIME_ROOT/wakeword/vosk-nl.zip" -d "$EVA_RUNTIME_ROOT/wakeword" && \
    rm -f "$EVA_RUNTIME_ROOT/wakeword/vosk-nl.zip" || true
fi

chown -R "$EVA_UID:$EVA_UID" "$EVA_RUNTIME_ROOT/wakeword"
chmod 0700 "$EVA_RUNTIME_ROOT/wakeword"

chown "$EVA_UID:$EVA_UID" \
  "$EVA_RUNTIME_ROOT" "$EVA_RUNTIME_ROOT/secrets" "$EVA_RUNTIME_ROOT/private" \
  "$EVA_RUNTIME_ROOT/private/restore-authority" "$EVA_RUNTIME_ROOT/work" \
  "$EVA_RUNTIME_ROOT/work/model-providers" "$EVA_RUNTIME_ROOT/hindsight-access" \
  "$EVA_RUNTIME_ROOT/identity" "$EVA_WIZARD_DIR" "$EVA_HOME/.codex"
chmod 0700 \
  "$EVA_RUNTIME_ROOT" "$EVA_RUNTIME_ROOT/secrets" "$EVA_RUNTIME_ROOT/private" \
  "$EVA_RUNTIME_ROOT/private/restore-authority" "$EVA_RUNTIME_ROOT/work" \
  "$EVA_RUNTIME_ROOT/work/model-providers" "$EVA_RUNTIME_ROOT/hindsight-access" \
  "$EVA_RUNTIME_ROOT/identity" "$EVA_WIZARD_DIR" "$EVA_HOME/.codex"

chmod 0755 "$EVA_RUNTIME_ROOT/pg-secret"
touch "$EVA_RUNTIME_ROOT/hindsight-access/auth.lock"
chown "$EVA_UID:$EVA_UID" "$EVA_RUNTIME_ROOT/hindsight-access/auth.lock"
chmod 0600 "$EVA_RUNTIME_ROOT/hindsight-access/auth.lock"

chown "$EVA_UID:65532" "$EVA_RUNTIME_ROOT/model-lease"
chmod 02750 "$EVA_RUNTIME_ROOT/model-lease"

# Kopieer eventuele vooraf gegenereerde headless koppel-hash vanaf de USB-seed.
if [ -f "$EVA_SEED_DIR/pairing.json" ] && [ ! -f "$EVA_WIZARD_DIR/pairing.json" ]; then
  install -m 0600 -o "$EVA_UID" -g "$EVA_UID" "$EVA_SEED_DIR/pairing.json" "$EVA_WIZARD_DIR/pairing.json"
fi

# Genereer het PostgreSQL-wachtwoord alvast zodat de container zonder tussenkomst kan draaien.
if [ ! -f "$EVA_RUNTIME_ROOT/secrets/postgres-password" ]; then
  PW=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
  printf '%s\n' "$PW" > "$EVA_RUNTIME_ROOT/secrets/postgres-password"
  chown "$EVA_UID:$EVA_UID" "$EVA_RUNTIME_ROOT/secrets/postgres-password"
  chmod 0600 "$EVA_RUNTIME_ROOT/secrets/postgres-password"
fi
install -m 0600 -o 999 -g 999 "$EVA_RUNTIME_ROOT/secrets/postgres-password" "$EVA_RUNTIME_ROOT/pg-secret/postgres-password"

# Docker images klaarzetten voor amd64 of arm64 (na starten van de wizard)
ARCH=$(uname -m)
POSTGRES_IMG="docker.io/library/postgres@sha256:472efd9a66f2b2f1a5aeb18b28de74332e6ef88c2b93a1a5d812fb6db67a5f60"
if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
  HINDSIGHT_IMG="ghcr.io/vectorize-io/hindsight@sha256:4b903ed2cbf029d6d305030b0005002d61d6a3b1d9b85867fb459351dcaa671c"
else
  HINDSIGHT_IMG="ghcr.io/vectorize-io/hindsight@sha256:aeb0831e2ec55e659d5e69baa064c1278113e523513b8f60a1f09c5cabe59b9b"
fi

cat > "$EVA_RUNTIME_ROOT/hindsight-starter.py" << 'EOF'
from pathlib import Path
import subprocess
import time

from eva.operations.model_credentials import (
    ModelCredentialLeaseError,
    check_codex_consumer_auth,
)

AUTH_TARGET = Path("/var/lib/eva2-local/hindsight-access/auth.json")
CONTAINER = "eva2-local-hindsight"

while True:
    try:
        check_codex_consumer_auth(AUTH_TARGET)
    except ModelCredentialLeaseError:
        time.sleep(3)
        continue
    res = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode == 0 and res.stdout.strip() != "true":
        subprocess.run(["docker", "start", CONTAINER], check=False)
    time.sleep(3)
EOF
chown "$EVA_UID:$EVA_UID" "$EVA_RUNTIME_ROOT/hindsight-starter.py"
chmod 0755 "$EVA_RUNTIME_ROOT/hindsight-starter.py"

VENV_PY="$EVA_HOME/.venvs/eva/bin/python"

cat > /etc/systemd/system/eva2-local-model-lease.service << EOF
[Unit]
Description=Eva2 local model access lease publisher
After=network.target

[Service]
User=$EVA_USER
Group=$EVA_USER
SupplementaryGroups=eva-runtime
WorkingDirectory=$EVA_REPO_DIR
ExecStart=$VENV_PY -m eva.operations.model_credentials --source $EVA_HOME/.codex/auth.json --target $EVA_RUNTIME_ROOT/model-lease/auth.json
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/eva2-local-hindsight-lease.service << EOF
[Unit]
Description=Eva2 local Hindsight Codex consumer lease publisher
After=network.target

[Service]
User=$EVA_USER
Group=$EVA_USER
WorkingDirectory=$EVA_REPO_DIR
ExecStart=$VENV_PY -m eva.operations.model_credentials --format codex-consumer --source $EVA_HOME/.codex/auth.json --target $EVA_RUNTIME_ROOT/hindsight-access/auth.json
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/eva2-local-hindsight-starter.service << EOF
[Unit]
Description=Eva2 local Hindsight auto-starter when Codex lease becomes valid
After=docker.service eva2-local-hindsight-lease.service
Requires=docker.service

[Service]
User=$EVA_USER
Group=docker
WorkingDirectory=$EVA_REPO_DIR
ExecStart=$VENV_PY $EVA_RUNTIME_ROOT/hindsight-starter.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/eva2-local-gateway.service << EOF
[Unit]
Description=Eva2 local authenticated MCP Gateway & Universal Capability Host
After=network.target

[Service]
User=$EVA_USER
Group=eva-runtime
WorkingDirectory=$EVA_REPO_DIR
ExecStart=$VENV_PY -m eva.bootstrap.appliance_mcp_gateway_cli --host 127.0.0.1 --port 44712 --runtime-root $EVA_RUNTIME_ROOT
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/eva2-local-core.service << EOF
[Unit]
Description=Eva2 local Living Core runtime
After=network.target docker.service eva2-local-gateway.service
Requires=docker.service
Wants=eva2-local-gateway.service

[Service]
User=$EVA_USER
Group=eva-runtime
WorkingDirectory=$EVA_REPO_DIR
EnvironmentFile=$EVA_RUNTIME_ROOT/runtime.env
ExecStart=$VENV_PY -m uvicorn eva.bootstrap.runtime:create_runtime_app --factory --host 127.0.0.1 --port 44710 --no-access-log
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/eva2-local-worker.service << EOF
[Unit]
Description=Eva2 local Delegated Research & Digital Worker
After=network.target docker.service eva2-local-gateway.service eva2-local-core.service
Wants=eva2-local-gateway.service

[Service]
User=$EVA_USER
Group=eva-runtime
WorkingDirectory=$EVA_REPO_DIR
EnvironmentFile=$EVA_RUNTIME_ROOT/runtime.env
ExecStart=$VENV_PY -m eva.bootstrap.delegated_work_service
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/eva2-local-owner.service << EOF
[Unit]
Description=Eva2 local owner dashboard without interactive login
After=network.target eva2-local-core.service

[Service]
User=$EVA_USER
Group=$EVA_USER
ExecStart=/usr/bin/node $EVA_REPO_DIR/deploy/dashboard/local-owner.mjs
LoadCredential=dashboard-token:$EVA_RUNTIME_ROOT/secrets/dashboard-token
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/eva2-local-telegram.service << EOF
[Unit]
Description=Eva2 private Telegram polling ingress
After=network-online.target eva2-local-core.service
Wants=network-online.target

[Service]
User=$EVA_USER
Group=eva-runtime
WorkingDirectory=$EVA_REPO_DIR
EnvironmentFile=$EVA_RUNTIME_ROOT/runtime.env
ExecStart=$VENV_PY -m eva.bootstrap.telegram_ingress
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/eva2-appliance-wizard.service << EOF
[Unit]
Description=Eva2 1-click Character, Module & Appliance Wizard
After=network-online.target docker.service
Wants=network-online.target

[Service]
User=$EVA_USER
Group=docker
SupplementaryGroups=eva-runtime
WorkingDirectory=$EVA_REPO_DIR
Environment=HOME=$EVA_HOME
ExecStart=$VENV_PY -m eva.bootstrap.appliance_wizard_cli --host 0.0.0.0 --port 8765 --workspace $EVA_WIZARD_DIR --runtime-root $EVA_RUNTIME_ROOT --repo-root $EVA_REPO_DIR --codex-auth-file $EVA_HOME/.codex/auth.json
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

set_step 98 "Stap 6/6 · Eva Setup Wizard starten..."
if [ -n "$EARLY_PID" ]; then
  kill "$EARLY_PID" 2>/dev/null || true
  wait "$EARLY_PID" 2>/dev/null || true
  EARLY_PID=""
fi

systemctl daemon-reload
systemctl enable --now \
  eva2-local-model-lease.service \
  eva2-local-hindsight-lease.service \
  eva2-local-hindsight-starter.service \
  eva2-local-gateway.service \
  eva2-appliance-wizard.service

beep_tone
sleep 0.2
beep_tone
echo "Eva Appliance Wizard draait op http://$(hostname).local:8765/pair"

# Pre-pull gepinde Docker images in de achtergrond zodat de wizard direct bereikbaar is.
docker pull "$POSTGRES_IMG" || true
docker pull "$HINDSIGHT_IMG" || true
